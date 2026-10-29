#!/usr/bin/env bash
set -uo pipefail
# Backup fleet repos: stash uncommitted work, push unpushed commits to backup branches.
# Runs every 150s from run-watchdog.sh. Abort on secret-scan failures.
# Improvements: dot-directory discovery, path dedup, tracked-files-only secret scanning.
# P2 FIX: JSON escaping for repo names and NUL-delimited internal protocol.

# No default (tracker 00fa6e4cb68b): with AESOP_ROOT unset this used to fall back to '.',
# so the secret scanner was looked up relative to whatever cwd the caller had. main()
# refuses to run instead. run-watchdog.sh exports AESOP_ROOT before calling this script.
AESOP_ROOT="${AESOP_ROOT:-}"

# Heartbeat-pollution guard (incident 2026-10-06/07): CONDUCTOR_ROOT used to
# default unconditionally to "$(dirname "$AESOP_ROOT")/conductor3". Every
# worktree (aesop-wt-*) and every test fixture lives as a direct child of the
# same $HOME as the real fleet-state root's parent, so that derivation
# silently resolved to the REAL fleet-state root for ANY AESOP_ROOT, not just the canonical
# ~/aesop tree -- letting test/lane runs overwrite the live watchdog
# heartbeat. Only auto-derive the sibling when AESOP_ROOT's basename is
# exactly "aesop" (the canonical tree's name); any other shape (a worktree,
# a test fixture) must pass CONDUCTOR_ROOT explicitly, or the heartbeat
# write below is skipped entirely -- same fail-closed contract as the
# existing "failure paths skip the write so staleness is detectable" rule.
if [ -n "${CONDUCTOR_ROOT:-}" ]; then
  : # explicit override always wins
elif [ "$(basename "$AESOP_ROOT")" = "aesop" ]; then
  CONDUCTOR_ROOT="$(dirname "$AESOP_ROOT")/conductor3"  # default sibling (canonical tree only)
else
  CONDUCTOR_ROOT=""  # non-canonical AESOP_ROOT: no silent derivation
fi
HEARTBEAT="${CONDUCTOR_ROOT:+$CONDUCTOR_ROOT/state/.watchdog-heartbeat}"
# Blocked-cycle marker (same CONDUCTOR_ROOT guard as HEARTBEAT): present while the
# last completed cycle had >=1 BLOCKED repo, holding the blocked repo names one per
# line. Lets consumers tell healthy from healthy-with-blocked. Removed by a clean cycle.
BLOCKED_MARKER="${CONDUCTOR_ROOT:+$CONDUCTOR_ROOT/state/.watchdog-blocked}"
LOG="$AESOP_ROOT/state/FLEET-BACKUP.log"
REPOS_STATUS="$AESOP_ROOT/state/.watchdog-repos.json"
# Git's well-known empty tree: the base of a root-inclusive range (SEM-4).
EMPTY_TREE_SHA="4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Timeout bounds (fix 20261029: prevent autodiscovery hang)
# Per-repo operations (git fetch, etc) timeout after this many seconds
REPO_OPERATION_TIMEOUT="${REPO_OPERATION_TIMEOUT:-60}"
# Entire autodiscovery loop timeout (so cycle fits in 5-min schedule)
AUTODISCOVERY_TIMEOUT="${AUTODISCOVERY_TIMEOUT:-240}"

# P2 FIX: script-global temp file handles (cycle-status JSON build + per-repo
# NUL-protocol scratch file), cleaned up unconditionally via trap below so an
# interrupted cycle (INT/TERM) or unexpected error never leaks mktemp files
# over the daemon's long-running lifecycle. Initialized empty so `rm -f`
# under `set -u` is always safe even if the trap fires before mktemp runs.
temp_json=""
temp_result=""
cleanup_temp_files() {
  rm -f "$temp_json" "$temp_result"
}
trap cleanup_temp_files EXIT INT TERM

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" | tee -a "$LOG"; }
# Diagnostics from inside process_repo: its stdout is the NUL verdict record, so
# these go to the log file and stderr only (SEM-6).
log_err() { echo "[$(ts)] $*" | tee -a "$LOG" >&2; }

# Heartbeat = attestation that a cycle COMPLETED (exit 0 or the WARN exit 3).
# Hard failure paths (exit 1/2) never call this, so their staleness stays visible.
write_heartbeat() {
  if [ -n "$HEARTBEAT" ]; then
    date +%s > "$HEARTBEAT" 2>/dev/null
  fi
}

write_blocked_marker() {
  if [ -n "$BLOCKED_MARKER" ]; then
    printf '%s' "$1" > "$BLOCKED_MARKER" 2>/dev/null
  fi
}

clear_blocked_marker() {
  if [ -n "$BLOCKED_MARKER" ]; then
    rm -f "$BLOCKED_MARKER" 2>/dev/null
  fi
}


# P2 FIX: JSON escape function for safe string interpolation in JSON
# Escapes backslash, quote, and control characters (newline, tab, carriage return, etc.)
json_escape() {
  local s="$1"
  # Escape backslash first (must be first to avoid double-escaping)
  s="${s//\\/\\\\}"
  # Escape double quote
  s="${s//\"/\\\"}"
  # Escape newline
  s="${s//$'\n'/\\n}"
  # Escape carriage return
  s="${s//$'\r'/\\r}"
  # Escape tab
  s="${s//$'\t'/\\t}"
  # Escape backspace, form feed, and other C0 controls
  s="${s//$'\b'/\\b}"
  s="${s//$'\f'/\\f}"
  printf '%s' "$s"
}

is_touched() {
  local repo="$1"
  [ ! -d "$repo/.git" ] && return 1
  (
    cd "$repo" || return 1
    [ -n "$(git status --porcelain 2>/dev/null)" ] && return 0
    [ -n "$(git log @{u}.. --oneline 2>/dev/null)" ] && return 0
    return 1
  )
}

# Fail-closed scanner resolution (tracker 00fa6e4cb68b). Prints the interpreter that runs
# tools/secret_scan.py. If the scanner file or every interpreter is missing, the reason
# is logged to FLEET-BACKUP.log and the function returns 1, so the caller refuses the
# push (repo -> BLOCKED). It used to return 0 here: an unscanned push.
resolve_scanner() {
  local label="$1"
  if [ ! -f "$AESOP_ROOT/tools/secret_scan.py" ]; then
    echo "[$(ts)] SECRET-SCAN $label: scanner missing ($AESOP_ROOT/tools/secret_scan.py); push refused (fail-closed)" >> "$LOG"
    return 1
  fi
  local py=""
  if command -v python3 >/dev/null 2>&1; then py=python3
  elif command -v python >/dev/null 2>&1; then py=python; fi
  if [ -z "$py" ]; then
    echo "[$(ts)] SECRET-SCAN $label: no python interpreter; push refused (fail-closed)" >> "$LOG"
    return 1
  fi
  printf '%s' "$py"
}

# NUL-delimited with core.quotepath off (tracker 00fa6e4cb68b): without -z git C-quotes
# non-ASCII names ("caf\303\251-keys.env"), the -f test then fails and the file is never
# scanned. NUL output is read with read -d '' in the callers.
# G (R-AUDIT-BKP-3): a failed listing makes the subshell exit non-zero (pipefail plus an
# explicit check on each git call), so the caller can refuse instead of scanning nothing.
get_tracked_modifications() {
  local repo="$1"
  (
    set -o pipefail
    cd "$repo" || exit 1
    { git -c core.quotepath=off diff -z --name-only HEAD 2>/dev/null || exit 1
      git -c core.quotepath=off ls-files -z -m 2>/dev/null || exit 1
    } | sort -zu
  )
}

get_untracked_files() {
  local repo="$1"
  (
    set -o pipefail
    cd "$repo" || exit 1
    git -c core.quotepath=off ls-files -z --others --exclude-standard 2>/dev/null | sort -zu
  )
}

scan_unpushed_commits() {
  local repo="$1"
  local unpushed_files
  local range_expr

  # Detect if upstream exists (no-upstream or detached HEAD scenario)
  local upstream list_rc
  upstream=$(cd "$repo" 2>/dev/null && git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || echo "")

  if [ -z "$upstream" ] || [ "$upstream" = "@{u}" ]; then
    # No upstream or detached HEAD: the push below sends HEAD's WHOLE history, root
    # included, so the scan base is the empty tree (SEM-4). The old bounded base
    # (HEAD~19..HEAD, from rev-list | tail -1) never scanned the root commit.
    log_err "WARN: Repository has no upstream or detached HEAD; scanning full history from the empty tree for $repo"
    range_expr="$EMPTY_TREE_SHA..HEAD"
    unpushed_files=$(cd "$repo" && git diff --name-only "$range_expr" 2>/dev/null); list_rc=$?
  else
    # Normal case: scan commits not yet pushed to upstream
    range_expr="@{u}..HEAD"
    unpushed_files=$(cd "$repo" && git diff --name-only @{u}..HEAD 2>/dev/null); list_rc=$?
  fi

  # Fail closed: a failed listing is a scanner error, never "nothing to scan".
  if [ "$list_rc" -ne 0 ]; then
    echo "[$(ts)] SECRET-SCAN $(basename "$repo"): cannot list range $range_expr (fail-closed)" >> "$LOG"
    return 1
  fi

  # F (R-AUDIT-BKP-3): an empty listing is NOT a pass. An add-then-remove secret leaves no
  # trace in the tip tree, so the scanner always runs over the range. It walks every blob
  # each commit introduced and reports CLEAN itself only when there is nothing to scan.

  # P2 wave-25 fix: scan the COMMITTED BLOB content of the unpushed range
  # (via secret_scan.py --range, which reads each changed path's git object
  # at the range's tip commit), NOT the current working-tree copy of these
  # files. A secret committed and then cleaned up in a LATER commit -- but
  # still present in the unpushed range being force-pushed to the backup
  # ref -- was previously invisible here, because the old code read
  # "$repo/$file" straight off disk (which no longer had the secret) instead
  # of the actual blob(s) the backup push carries. --range is also robust to
  # deleted files (git diff --diff-filter=d inside secret_scan.py already
  # excludes them from the file list it scans).
  # Scanner output goes to a file (process_repo's stdout is the NUL protocol);
  # its HIGH/FOUND/FATAL lines are appended to FLEET-BACKUP.log so the BLOCKED
  # repo is named with file:line + rule. secret_scan.py masks matched values.
  # A missing scanner or interpreter returns 1 (refused), never 0 (tracker 00fa6e4cb68b).
  local py scan_out scan_rc
  py=$(resolve_scanner "$(basename "$repo")") || return 1
  scan_out=$(mktemp) || { echo "[$(ts)] SECRET-SCAN $(basename "$repo"): cannot capture scanner output (fail-closed)" >> "$LOG"; return 1; }
  "$py" "$AESOP_ROOT/tools/secret_scan.py" --range "$range_expr" --repo "$repo" > "$scan_out" 2>&1
  scan_rc=$?
  grep -E '^(HIGH |FOUND: |FATAL)' "$scan_out" 2>/dev/null | while IFS= read -r line; do
    echo "[$(ts)] SECRET-SCAN $(basename "$repo"): $line" >> "$LOG"
  done
  rm -f "$scan_out"
  return $scan_rc
}

scan_tracked_files() {
  local repo="$1"
  local -a file_paths=()
  local repo_win file

  # Portable path derivation (not Git-Bash pwd -W only)
  repo_win=$(cd "$repo" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null || echo "$repo")

  # Tracked modifications, then untracked files (ITEM 1 fix). NUL-delimited so no
  # name is C-quoted or split (tracker 00fa6e4cb68b: a non-ASCII name was dropped here).
  # G (R-AUDIT-BKP-3): capture both listings to a file and check their status. A process
  # substitution would hide a failed listing, and the scan would then pass on zero files.
  local list_file list_rc
  list_file=$(mktemp) || { echo "[$(ts)] SECRET-SCAN $(basename "$repo"): cannot capture file listing (fail-closed)" >> "$LOG"; return 1; }
  { get_tracked_modifications "$repo" && get_untracked_files "$repo"; } > "$list_file"
  list_rc=$?
  if [ "$list_rc" -ne 0 ]; then
    rm -f "$list_file"
    echo "[$(ts)] SECRET-SCAN $(basename "$repo"): file listing failed (fail-closed)" >> "$LOG"
    return 1
  fi
  while IFS= read -r -d '' file; do
    if [ -n "$file" ] && [ -f "$repo/$file" ]; then
      file_paths+=("$repo_win/$file")
    fi
  done < "$list_file"
  rm -f "$list_file"

  if [ ${#file_paths[@]} -eq 0 ]; then
    return 0
  fi

  # ITEM 2 fix: use "${file_paths[@]}" to properly quote array elements.
  # Fail closed: no scanner or no interpreter means refused, not passed (00fa6e4cb68b).
  local py scan_out scan_rc
  py=$(resolve_scanner "$(basename "$repo")") || return 1
  # Same capture as scan_unpushed_commits: HIGH/FOUND/FATAL lines -> FLEET-BACKUP.log.
  scan_out=$(mktemp) || { echo "[$(ts)] SECRET-SCAN $(basename "$repo"): cannot capture scanner output (fail-closed)" >> "$LOG"; return 1; }
  "$py" "$AESOP_ROOT/tools/secret_scan.py" "${file_paths[@]}" > "$scan_out" 2>&1
  scan_rc=$?
  grep -E '^(HIGH |FOUND: |FATAL)' "$scan_out" 2>/dev/null | while IFS= read -r line; do
    echo "[$(ts)] SECRET-SCAN $(basename "$repo"): $line" >> "$LOG"
  done
  rm -f "$scan_out"
  return $scan_rc
}

get_default_branch() {
  local repo="$1"
  (cd "$repo" && git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's/refs\/remotes\/origin\///' || echo "master")
}

discover_repos() {
  local processed_paths="$1"
  local discovery_file="$2"
  : > "$discovery_file"
  for dir in ~/.* ~/* ~/dev/*; do
    base=$(basename "$dir")
    [ "$base" = "." ] || [ "$base" = ".." ] && continue
    [ ! -d "$dir/.git" ] && continue
    real_dir=$(cd "$dir" && pwd 2>/dev/null)
    if [ -z "$real_dir" ]; then continue; fi
    if echo "$processed_paths" | grep -Fxq "$real_dir"; then
      continue
    fi
    if is_touched "$dir"; then
      printf '%s\n' "$dir"
    fi
  done >> "$discovery_file"
}

process_repo() {
  local repo="$1"
  local name=$(basename "$repo")
  local default=$(get_default_branch "$repo")

  (
    cd "$repo" || exit 0
    git fetch -q origin 2>/dev/null
    local uncommitted=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')

    if [ "$uncommitted" -gt 0 ]; then
      TMPIDX=$(mktemp)
      GIT_INDEX_FILE="$TMPIDX" git read-tree HEAD 2>/dev/null
      GIT_INDEX_FILE="$TMPIDX" git add -A 2>/dev/null
      TREE=$(GIT_INDEX_FILE="$TMPIDX" git write-tree 2>/dev/null)
      rm -f "$TMPIDX"
      LOCAL=$(git rev-parse HEAD 2>/dev/null)
      HEADTREE=$(git rev-parse 'HEAD^{tree}' 2>/dev/null)
      # H (R-AUDIT-BKP-3): uncommitted work exists but its tree could not be built. Falling
      # through would report CLEAN with the work unsnapshotted, so this is FAILED.
      if [ -z "$TREE" ]; then
        log_err "ERROR: write-tree failed for $name with uncommitted changes; not reporting CLEAN"
        printf 'FAILED\0%s\n' "$name"
        exit 0
      fi
      if [ "$TREE" != "$HEADTREE" ]; then
        # SEM-3: the WIP commit's parent is local HEAD, so the push carries every
        # unpushed commit as well as the working tree. Scan BOTH before anything is
        # committed or pushed; either BLOCKED refuses the snapshot.
        if ! scan_unpushed_commits "$repo" || ! scan_tracked_files "$repo"; then
          printf 'BLOCKED\0%s\n' "$name"
          exit 0
        fi
        COMMIT=$(git commit-tree "$TREE" -p "$LOCAL" -m "wip $(ts) — $uncommitted files" 2>/dev/null)
        # SEM-7: a failed commit-tree leaves COMMIT empty. Pushing that sends the refspec
        # ":refs/heads/backup/wip-DATE", a DELETE of today's backup on origin. Refuse it.
        if [ -z "$COMMIT" ]; then
          log_err "ERROR: commit-tree produced no commit for $name; WIP snapshot not pushed"
          printf 'FAILED\0%s\n' "$name"
          exit 0
        fi
        WIPREF="backup/wip-$(date +%Y%m%d)"
        if git push -qf origin "$COMMIT:refs/heads/$WIPREF" 2>/dev/null; then
          printf 'SNAPSHOTTED\0%s\n' "$name"
        else
          printf 'FAILED\0%s\n' "$name"
        fi
        exit 0
      fi
    fi

    # Detect if upstream exists before trying to count unpushed commits
    local upstream
    upstream=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || echo "")

    local unpushed
    if [ -z "$upstream" ] || [ "$upstream" = "@{u}" ]; then
      # No upstream or detached HEAD: count recent commits instead (last 20)
      log_err "WARN: Repository has no upstream or detached HEAD; falling back to recent commits count for $name"
      unpushed=$(git log --oneline -20 2>/dev/null | wc -l | tr -d ' ')
    else
      # Normal case: count commits not yet pushed to upstream
      unpushed=$(git log @{u}.. --oneline 2>/dev/null | wc -l | tr -d ' ')
    fi

    if [ "$unpushed" -gt 0 ]; then
      local branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
      [ "$branch" = "HEAD" ] && branch="$default"

      # Any push failure is FAILED, never CLEAN or SNAPSHOTTED/PUSHED (SEM-7).
      if [ "$branch" = "$default" ]; then
        WIPREF="backup/master-wip-$(date +%Y%m%d)"
        if ! scan_unpushed_commits "$repo"; then
          printf 'BLOCKED\0%s\n' "$name"
          exit 0
        fi
        if git push -qf origin "HEAD:refs/heads/$WIPREF" 2>/dev/null; then
          printf 'SNAPSHOTTED\0%s\n' "$name"
        else
          printf 'FAILED\0%s\n' "$name"
        fi
        exit 0
      else
        if ! scan_unpushed_commits "$repo"; then
          printf 'BLOCKED\0%s\n' "$name"
          exit 0
        fi
        if git push -q origin "$branch" 2>/dev/null; then
          printf 'PUSHED\0%s\n' "$name"
        else
          printf 'FAILED\0%s\n' "$name"
        fi
        exit 0
      fi
    fi

    printf 'CLEAN\0%s\n' "$name"
  )
}

main() {
  # Fail closed (tracker 00fa6e4cb68b): no AESOP_ROOT means no secret scanner can be
  # located and no state log can be written. Refuse, with no heartbeat, before any work.
  if [ -z "$AESOP_ROOT" ]; then
    echo "backup-fleet: AESOP_ROOT is not set; refusing to run (fail-closed: the secret scanner cannot be located)" >&2
    exit 1
  fi
  log "=== cycle start ==="
  # FIX 20261029: Write heartbeat at cycle START (before autodiscovery) so even a
  # hanging cycle shows the daemon is alive. If autodiscovery hangs for 22 min, the
  # heartbeat at START ensures the next cycle's monitor sees a fresh timestamp and
  # doesn't incorrectly restart the hung daemon.
  write_heartbeat
  temp_json=$(mktemp) || {
    # Hard failure: the cycle cannot stage its status file. Exit 1 WITHOUT a
    # heartbeat so the failed cycle reads as stale.
    log "ERROR: cannot create cycle scratch file; cycle aborted (exit 1, no heartbeat)"
    exit 1
  }
  echo "[" > "$temp_json"
  blocked_names=""
  first=1
  processed_paths=""
  # FIX #5: track whether ANY repo was BLOCKED (secret-scan gate failure)
  # this cycle, across BOTH discovery loops below (config-repos + autodiscovery).
  # Exit code contract (mirrored in daemons/run-watchdog.sh, which consumes
  # this script's exit code):
  #   0 = clean cycle, no repo blocked
  #   3 = "blocked cycle" -- at least one repo was BLOCKED by the secret-scan
  #       gate; distinct from other non-zero (crash) exit codes so a
  #       supervisor keying on $? can WARN + keep the daemon alive instead
  #       of treating it as a hard failure. Does NOT change what "blocked"
  #       means or the block-decision logic -- BLOCKED still just means
  #       process_repo's scan_tracked_files/scan_unpushed_commits gate failed.
  any_blocked=0

  # AUDIT FIX 1: Check if aesop.config.json exists and has repos array
  repos_to_scan=""
  config_file="$AESOP_ROOT/aesop.config.json"
  if [ -f "$config_file" ]; then
    # Find Python interpreter (portable: prefer python3, fallback to python)
    python_exe=""
    if command -v python3 >/dev/null 2>&1; then
      python_exe="python3"
    elif command -v python >/dev/null 2>&1; then
      python_exe="python"
    fi

    if [ -n "$python_exe" ]; then
      # Parse repos array from config using Python (not grep)
      # Note: Strip trailing \r (CRLF compatibility) and empty lines
      repos_to_scan=$($python_exe -c "
import json, sys
try:
  with open(sys.argv[1], 'r') as f:
    config = json.load(f)
    repos = config.get('repos', [])
    if isinstance(repos, list) and len(repos) > 0:
      for repo in repos:
        if isinstance(repo, dict) and 'path' in repo:
          print(repo['path'])
except Exception as e:
  pass
" "$config_file" 2>/dev/null | tr -d '\r')
    fi
  fi

  if [ -n "$repos_to_scan" ]; then
    # AUDIT FIX 1: Use explicit repos from config
    log "Loading repos from config file"
    while IFS= read -r dir; do
      [ -z "$dir" ] && continue
      # Validate repo path exists
      if [ ! -d "$dir/.git" ]; then
        log "WARN: configured repo not found or not a git repo (skipping): $dir"
        continue
      fi
      # Normalize path to detect duplicates
      real_dir=$(cd "$dir" && pwd 2>/dev/null)
      if [ -z "$real_dir" ]; then continue; fi
      if echo "$processed_paths" | grep -Fxq "$real_dir"; then
        continue
      fi
      processed_paths="$processed_paths
$real_dir"

      if is_touched "$dir"; then
        temp_result=$(mktemp)
        # Wrap process_repo in bounded timeout (per-repo operation timeout)
        timeout "$REPO_OPERATION_TIMEOUT" process_repo "$dir" > "$temp_result" 2>&1
        local proc_exit=$?
        if [ "$proc_exit" -eq 124 ]; then
          # Timeout hit: log as TIMEOUT status
          log_err "TIMEOUT: $(basename "$dir") exceeded ${REPO_OPERATION_TIMEOUT}s timeout"
          printf 'TIMEOUT\0%s\n' "$(basename "$dir")" > "$temp_result"
        fi
        state=$(awk 'BEGIN{RS="\0"} NR==1 {print}' "$temp_result")
        name=$(awk 'BEGIN{RS="\0"} NR==2 {print}' "$temp_result" | tr -d '\n')
        rm -f "$temp_result"
        [ -z "$state" ] && continue
        [ "$state" = "BLOCKED" ] && { any_blocked=1; blocked_names="$blocked_names$name"$'\n'; }
        if [ "$first" = 1 ]; then
          first=0
        else
          echo "," >> "$temp_json"
        fi
        printf '{"repo":"%s","state":"%s","age":"%s"}' "$(json_escape "$name")" "$(json_escape "$state")" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$temp_json"
        log "$state: $name"
      fi
    done <<< "$repos_to_scan"
  else
    # AUDIT FIX 1: Fall back to autodiscovery only when no repos configured
    log "No repos configured, using autodiscovery"
    local discovery_file
    discovery_file=$(mktemp) || {
      log "ERROR: cannot create autodiscovery scratch file; skipping autodiscovery"
      echo "]" >> "$temp_json"
      mv "$temp_json" "$REPOS_STATUS"
      exit 0
    }
    timeout "$AUTODISCOVERY_TIMEOUT" discover_repos "$processed_paths" "$discovery_file" 2>&1 || {
      local disc_exit=$?
      if [ "$disc_exit" -eq 124 ]; then
        log "WARN: autodiscovery timed out after ${AUTODISCOVERY_TIMEOUT}s; processing discovered repos"
      else
        log "WARN: autodiscovery failed (exit $disc_exit)"
      fi
    }
    while IFS= read -r dir; do
      [ -z "$dir" ] && continue
      real_dir=$(cd "$dir" && pwd 2>/dev/null)
      if [ -z "$real_dir" ]; then continue; fi
      processed_paths="$processed_paths
$real_dir"
      temp_result=$(mktemp)
      timeout "$REPO_OPERATION_TIMEOUT" process_repo "$dir" > "$temp_result" 2>&1
      local proc_exit=$?
      if [ "$proc_exit" -eq 124 ]; then
        log_err "TIMEOUT: $(basename "$dir") exceeded ${REPO_OPERATION_TIMEOUT}s timeout"
        printf 'TIMEOUT\0%s\n' "$(basename "$dir")" > "$temp_result"
      fi
      state=$(awk 'BEGIN{RS="\0"} NR==1 {print}' "$temp_result")
      name=$(awk 'BEGIN{RS="\0"} NR==2 {print}' "$temp_result" | tr -d '\n')
      rm -f "$temp_result"
      [ -z "$state" ] && continue
      [ "$state" = "BLOCKED" ] && { any_blocked=1; blocked_names="$blocked_names$name"$'\n'; }
      if [ "$first" = 1 ]; then
        first=0
      else
        echo "," >> "$temp_json"
      fi
      printf '{"repo":"%s","state":"%s","age":"%s"}' "$(json_escape "$name")" "$(json_escape "$state")" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$temp_json"
      log "$state: $name"
    done < "$discovery_file"
    rm -f "$discovery_file"
  fi

  echo "" >> "$temp_json"
  echo "]" >> "$temp_json"
  if ! mv "$temp_json" "$REPOS_STATUS"; then
    # Hard failure: the per-repo snapshot could not be persisted. Exit 1 with no
    # heartbeat -- a cycle whose state was not recorded did not complete.
    log "ERROR: cannot persist $REPOS_STATUS; cycle aborted (exit 1, no heartbeat)"
    exit 1
  fi

  # FIX #5: exit 3 (not 0) when >=1 repo was BLOCKED this cycle, so a
  # supervisor keying on $? can distinguish "blocked cycle" from a fully
  # clean one. See the any_blocked contract comment at the top of main().
  # Both completed outcomes (exit 0 and exit 3) write the heartbeat: the cycle
  # DID complete work (other repos were snapshotted, .watchdog-repos.json written).
  # A BLOCKED cycle additionally records the blocked repo names in
  # .watchdog-blocked so consumers can tell healthy from healthy-with-blocked.
  if [ "$any_blocked" -eq 1 ]; then
    log "=== cycle end (BLOCKED: >=1 repo blocked by secret-scan gate; exit 3) ==="
    write_blocked_marker "$blocked_names"
    write_heartbeat
    exit 3
  fi

  clear_blocked_marker
  log "=== cycle end ==="
  # Heartbeat is written HERE, after the cycle actually completed -- not before
  # main() runs. Written up front, a crash or hang inside main() still left a fresh
  # timestamp, so selfheal.sh saw a healthy age, never restarted the daemon, and
  # fleet backups halted silently. A heartbeat must attest to work DONE, not work
  # attempted. Hard failure paths (exit 1/2) deliberately do not write it.
  # write_heartbeat is a no-op when CONDUCTOR_ROOT could not be safely resolved
  # (see the heartbeat-pollution guard above).
  write_heartbeat
  exit 0
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
