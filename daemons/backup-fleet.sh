#!/usr/bin/env bash
set -uo pipefail
# Backup fleet repos: stash uncommitted work, push unpushed commits to backup branches.
# Runs every 150s from run-watchdog.sh. Abort on secret-scan failures.
# Improvements: dot-directory discovery, path dedup, tracked-files-only secret scanning.
# P2 FIX: JSON escaping for repo names and NUL-delimited internal protocol.

AESOP_ROOT="${AESOP_ROOT:-.}"
HEARTBEAT="$AESOP_ROOT/state/.watchdog-heartbeat"
LOG="$AESOP_ROOT/state/FLEET-BACKUP.log"
REPOS_STATUS="$AESOP_ROOT/state/.watchdog-repos.json"

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

date +%s > "$HEARTBEAT" 2>/dev/null
ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" | tee -a "$LOG"; }

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

get_tracked_modifications() {
  local repo="$1"
  (
    cd "$repo" || return 1
    git diff --name-only HEAD 2>/dev/null
    git ls-files -m 2>/dev/null
  ) | sort -u
}

get_untracked_files() {
  local repo="$1"
  (
    cd "$repo" || return 1
    git ls-files --others --exclude-standard 2>/dev/null
  ) | sort -u
}

scan_unpushed_commits() {
  local repo="$1"
  local unpushed_files
  local range_expr

  # Detect if upstream exists (no-upstream or detached HEAD scenario)
  local upstream
  upstream=$(cd "$repo" 2>/dev/null && git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || echo "")

  if [ -z "$upstream" ] || [ "$upstream" = "@{u}" ]; then
    # No upstream or detached HEAD: fallback to scanning recent commits (last 20)
    # Use git rev-list to get the base commit 20 commits back, then use git diff
    log "WARN: Repository has no upstream or detached HEAD; falling back to bounded commit range scan for $repo"
    local base
    base=$(cd "$repo" && git rev-list --max-count=20 HEAD 2>/dev/null | tail -1)
    if [ -n "$base" ] && [ "$base" != "$(cd "$repo" && git rev-parse HEAD 2>/dev/null)" ]; then
      # Multiple commits exist: scan from base to HEAD
      range_expr="$base..HEAD"
      unpushed_files=$(cd "$repo" && git diff --name-only "$range_expr" 2>/dev/null | sort -u)
    elif [ -n "$base" ]; then
      # Single commit (or base == HEAD): diff against the empty tree so the
      # range form below (empty_tree..HEAD) still works for a root commit
      # that has no parent to diff against.
      local empty_tree
      empty_tree=$(cd "$repo" && git hash-object -t tree /dev/null 2>/dev/null || echo "4b825dc642cb6eb9a060e54bf8d69288fbee4904")
      range_expr="$empty_tree..HEAD"
      unpushed_files=$(cd "$repo" && git diff --name-only "$range_expr" 2>/dev/null | sort -u)
    else
      unpushed_files=""
    fi
  else
    # Normal case: scan commits not yet pushed to upstream
    range_expr="@{u}..HEAD"
    unpushed_files=$(cd "$repo" && git diff --name-only @{u}..HEAD 2>/dev/null | sort -u)
  fi

  if [ -z "$unpushed_files" ]; then
    return 0
  fi

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
  if [ -f "$AESOP_ROOT/tools/secret_scan.py" ]; then
    if command -v python3 >/dev/null 2>&1; then
      python3 "$AESOP_ROOT/tools/secret_scan.py" --range "$range_expr" --repo "$repo" >/dev/null 2>&1
      return $?
    elif command -v python >/dev/null 2>&1; then
      python "$AESOP_ROOT/tools/secret_scan.py" --range "$range_expr" --repo "$repo" >/dev/null 2>&1
      return $?
    fi
  fi

  return 0
}

scan_tracked_files() {
  local repo="$1"
  local tracked_files
  local untracked_files
  local -a file_paths=()
  local repo_win

  # Portable path derivation (not Git-Bash pwd -W only)
  repo_win=$(cd "$repo" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null || echo "$repo")

  tracked_files=$(get_tracked_modifications "$repo")
  untracked_files=$(get_untracked_files "$repo")

  # Collect tracked modifications
  if [ -n "$tracked_files" ]; then
    while IFS= read -r file; do
      if [ -n "$file" ] && [ -f "$repo/$file" ]; then
        file_paths+=("$repo_win/$file")
      fi
    done <<EOF
$tracked_files
EOF
  fi

  # Collect untracked files (ITEM 1 fix)
  if [ -n "$untracked_files" ]; then
    while IFS= read -r file; do
      if [ -n "$file" ] && [ -f "$repo/$file" ]; then
        file_paths+=("$repo_win/$file")
      fi
    done <<EOF
$untracked_files
EOF
  fi

  if [ ${#file_paths[@]} -eq 0 ]; then
    return 0
  fi

  if [ -f "$AESOP_ROOT/tools/secret_scan.py" ]; then
    # ITEM 2 fix: use "${file_paths[@]}" to properly quote array elements
    # Defect (a) fix: use python3||python probe instead of bare python (portable to python3-only systems)
    if command -v python3 >/dev/null 2>&1; then
      python3 "$AESOP_ROOT/tools/secret_scan.py" "${file_paths[@]}" >/dev/null 2>&1
      return $?
    elif command -v python >/dev/null 2>&1; then
      python "$AESOP_ROOT/tools/secret_scan.py" "${file_paths[@]}" >/dev/null 2>&1
      return $?
    fi
  fi

  return 0
}

get_default_branch() {
  local repo="$1"
  (cd "$repo" && git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's/refs\/remotes\/origin\///' || echo "master")
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
      if [ -n "$TREE" ] && [ "$TREE" != "$HEADTREE" ]; then
        COMMIT=$(git commit-tree "$TREE" -p "$LOCAL" -m "wip $(ts) — $uncommitted files" 2>/dev/null)
        WIPREF="backup/wip-$(date +%Y%m%d)"
        if scan_tracked_files "$repo"; then
          if git push -qf origin "$COMMIT:refs/heads/$WIPREF" 2>/dev/null; then
            printf 'SNAPSHOTTED\0%s\n' "$name"
            exit 0
          fi
        else
          printf 'BLOCKED\0%s\n' "$name"
          exit 0
        fi
      fi
    fi

    # Detect if upstream exists before trying to count unpushed commits
    local upstream
    upstream=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || echo "")

    local unpushed
    if [ -z "$upstream" ] || [ "$upstream" = "@{u}" ]; then
      # No upstream or detached HEAD: count recent commits instead (last 20)
      log "WARN: Repository has no upstream or detached HEAD; falling back to recent commits count for $name"
      unpushed=$(git log --oneline -20 2>/dev/null | wc -l | tr -d ' ')
    else
      # Normal case: count commits not yet pushed to upstream
      unpushed=$(git log @{u}.. --oneline 2>/dev/null | wc -l | tr -d ' ')
    fi

    if [ "$unpushed" -gt 0 ]; then
      local branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
      [ "$branch" = "HEAD" ] && branch="$default"

      if [ "$branch" = "$default" ]; then
        WIPREF="backup/master-wip-$(date +%Y%m%d)"
        if scan_unpushed_commits "$repo"; then
          if git push -qf origin "HEAD:refs/heads/$WIPREF" 2>/dev/null; then
            printf 'SNAPSHOTTED\0%s\n' "$name"
            exit 0
          fi
        else
          printf 'BLOCKED\0%s\n' "$name"
          exit 0
        fi
      else
        if scan_unpushed_commits "$repo"; then
          if git push -q origin "$branch" 2>/dev/null; then
            printf 'PUSHED\0%s\n' "$name"
            exit 0
          fi
        else
          printf 'BLOCKED\0%s\n' "$name"
          exit 0
        fi
      fi
    fi

    printf 'CLEAN\0%s\n' "$name"
  )
}

main() {
  log "=== cycle start ==="
  temp_json=$(mktemp)
  echo "[" > "$temp_json"
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
        process_repo "$dir" > "$temp_result"
        state=$(awk 'BEGIN{RS="\0"} NR==1 {print}' "$temp_result")
        name=$(awk 'BEGIN{RS="\0"} NR==2 {print}' "$temp_result" | tr -d '\n')
        rm -f "$temp_result"
        [ -z "$state" ] && continue
        [ "$state" = "BLOCKED" ] && any_blocked=1
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
    # Discover all git repos: scan home dot-directories, root directories, dev/ subdirectory
    # This pattern includes ~/.* (dot-dirs like .claude), ~/* (home root), ~/dev/* (dev subtree)
    for dir in ~/.* ~/* ~/dev/*; do
      base=$(basename "$dir")
      [ "$base" = "." ] || [ "$base" = ".." ] && continue
      [ ! -d "$dir/.git" ] && continue

      # Normalize path to detect duplicates (e.g., .claude vs .claude/)
      real_dir=$(cd "$dir" && pwd 2>/dev/null)
      if [ -z "$real_dir" ]; then continue; fi
      if echo "$processed_paths" | grep -Fxq "$real_dir"; then
        continue
      fi
      processed_paths="$processed_paths
$real_dir"

      if is_touched "$dir"; then
        # P0 FIX: Eliminate $() command substitution to preserve NUL bytes in protocol
        # Direct redirect maintains NUL delimiters (process_repo emits STATE\0name format)
        temp_result=$(mktemp)
        process_repo "$dir" > "$temp_result"
        # Read first field (state) and second field (name) using NUL delimiter
        # Portable solution: use awk with RS="\0" instead of GNU sed \x0 (BSD/macOS compatible)
        state=$(awk 'BEGIN{RS="\0"} NR==1 {print}' "$temp_result")
        name=$(awk 'BEGIN{RS="\0"} NR==2 {print}' "$temp_result" | tr -d '\n')
        rm -f "$temp_result"
        [ -z "$state" ] && continue
        [ "$state" = "BLOCKED" ] && any_blocked=1
        if [ "$first" = 1 ]; then
          first=0
        else
          echo "," >> "$temp_json"
        fi
        # P2 FIX: JSON-escape all interpolated values before emitting
        # P2 FIX: Portable date format (not GNU-only date -Iseconds)
        printf '{"repo":"%s","state":"%s","age":"%s"}' "$(json_escape "$name")" "$(json_escape "$state")" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$temp_json"
        log "$state: $name"
      fi
    done
  fi

  echo "" >> "$temp_json"
  echo "]" >> "$temp_json"
  mv "$temp_json" "$REPOS_STATUS"

  # FIX #5: exit 3 (not 0) when >=1 repo was BLOCKED this cycle, so a
  # supervisor keying on $? can distinguish "blocked cycle" from a fully
  # clean one. See the any_blocked contract comment at the top of main().
  if [ "$any_blocked" -eq 1 ]; then
    log "=== cycle end (BLOCKED: >=1 repo blocked by secret-scan gate; exit 3) ==="
    exit 3
  fi

  log "=== cycle end ==="
  exit 0
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
