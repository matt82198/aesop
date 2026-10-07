#!/usr/bin/env bash
# INDEX: Incident forensics; behavior reconstruction (read-only git plumbing)
# agent-forensics.sh — incident forensics tool
# Reconstruct agent behavior as of a commit using git plumbing.
# Usage:
#   bash tools/agent-forensics.sh <commit>
#   bash tools/agent-forensics.sh --diff <commitA> <commitB>
#
# Prints:
#   - Commit header (hash, date, subject)
#   - Rules snapshot (docs/CARDINAL-RULES.md)
#   - CLAUDE.md (if present)
#   - STATE.md (if present)
#   - Last 30 lines of BUILDLOG.md (if tracked)
#   - For --diff: behavior changes between two commits
#
# Exit codes: 0 on success, 1 on error (never raw git stack traces).
# Requires: git, head, tail, wc, grep
# CRLF-safe: no line continuations, Git Bash + Linux compatible.

set -e

show_error() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

# Prints usage text only (no exit); callers decide the stream/exit code so
# that `--help`/`-h` (least-surprise: exit 0, stdout) and genuine usage
# errors (exit 1, stderr) can share one message without sharing behavior.
show_usage() {
  cat <<'EOF'
Usage:
  bash tools/agent-forensics.sh <commit>              Forensics at <commit>
  bash tools/agent-forensics.sh --diff <commitA> <commitB>   Behavior diff
EOF
}

# --- Commit header (hash, date, subject) ---
print_commit_header() {
  local commit="$1"

  # Verify commit exists
  if ! git cat-file -t "$commit" >/dev/null 2>&1; then
    show_error "unknown commit: $commit"
  fi

  local hash
  local date
  local subject

  hash=$(git rev-parse "$commit" 2>/dev/null || show_error "commit parse failed: $commit")
  date=$(git log -1 --format=%ai "$commit" 2>/dev/null || show_error "failed to read commit date")
  subject=$(git log -1 --format=%s "$commit" 2>/dev/null || show_error "failed to read commit subject")

  printf '# Forensics @ %s\n\n' "$hash"
  printf 'Commit:  %s\n' "$hash"
  printf 'Date:    %s\n' "$date"
  printf 'Subject: %s\n\n' "$subject"
}

# --- Print first ~40 lines of docs/CARDINAL-RULES.md ---
print_rules() {
  local commit="$1"

  printf '## Rules (docs/CARDINAL-RULES.md)\n\n'

  if ! git cat-file -e "$commit:docs/CARDINAL-RULES.md" 2>/dev/null; then
    printf '(file not present at this commit)\n\n'
    return
  fi

  git show "$commit:docs/CARDINAL-RULES.md" 2>/dev/null | head -40

  # Check if file was truncated
  local total_lines
  total_lines=$(git show "$commit:docs/CARDINAL-RULES.md" 2>/dev/null | wc -l)
  if [ "$total_lines" -gt 40 ]; then
    printf '\n[... %d more lines; see git show %s:docs/CARDINAL-RULES.md]\n' "$((total_lines - 40))" "$commit"
  fi
  printf '\n\n'
}

# --- Print CLAUDE.md if present ---
print_claude_md() {
  local commit="$1"

  printf '## CLAUDE.md\n\n'

  if ! git cat-file -e "$commit:CLAUDE.md" 2>/dev/null; then
    printf '(not present at this commit)\n\n'
    return
  fi

  git show "$commit:CLAUDE.md" 2>/dev/null
  printf '\n\n'
}

# --- Print STATE.md if present ---
print_state_md() {
  local commit="$1"

  printf '## STATE.md\n\n'

  if ! git cat-file -e "$commit:STATE.md" 2>/dev/null; then
    printf '(not present at this commit)\n\n'
    return
  fi

  git show "$commit:STATE.md" 2>/dev/null
  printf '\n\n'
}

# --- Print last 30 lines of BUILDLOG.md ---
print_buildlog() {
  local commit="$1"

  printf '## BUILDLOG.md (last 30 lines)\n\n'

  if ! git cat-file -e "$commit:BUILDLOG.md" 2>/dev/null; then
    printf '(not tracked at this commit)\n\n'
    return
  fi

  git show "$commit:BUILDLOG.md" 2>/dev/null | tail -30
  printf '\n\n'
}

# --- Diff behavior between two commits ---
diff_behavior() {
  local commitA="$1"
  local commitB="$2"

  # Verify both commits exist
  if ! git cat-file -t "$commitA" >/dev/null 2>&1; then
    show_error "unknown commit: $commitA"
  fi
  if ! git cat-file -t "$commitB" >/dev/null 2>&1; then
    show_error "unknown commit: $commitB"
  fi

  printf '# Behavior Diff\n\n'
  printf '%s -> %s\n\n' "$(git rev-parse "$commitA")" "$(git rev-parse "$commitB")"

  # Files that affect behavior: rules, CLAUDE.md, STATE.md, hooks, monitor/CHARTER.md
  local behavior_files
  behavior_files="CLAUDE.md STATE.md docs/CARDINAL-RULES.md docs/DISPATCH-MODEL.md docs/GOVERNANCE.md hooks/ monitor/CHARTER.md"

  printf '## Changes in behavior-controlling files\n\n'

  local has_changes=0
  for file in $behavior_files; do
    if git diff "$commitA" "$commitB" --name-only -- "$file" 2>/dev/null | grep -q .; then
      has_changes=1
      printf '### %s\n\n' "$file"
      git diff "$commitA" "$commitB" -- "$file" 2>/dev/null | head -100
      printf '\n'
    fi
  done

  if [ "$has_changes" -eq 0 ]; then
    printf '(no changes in behavior-controlling files)\n\n'
  fi
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  # --- Main dispatch ---
  case "${1:-}" in
    --diff)
      if [ $# -lt 3 ]; then
        show_error "--diff requires two commits: --diff <commitA> <commitB>"
      fi
      diff_behavior "$2" "$3"
      ;;
    --help|-h)
      # Requested help is not an error: usage to stdout, exit 0.
      show_usage
      exit 0
      ;;
    '')
      # Missing args is a genuine usage error: usage to stderr, exit 1.
      show_usage >&2
      exit 1
      ;;
    *)
      print_commit_header "$1"
      print_rules "$1"
      print_claude_md "$1"
      print_state_md "$1"
      print_buildlog "$1"
      ;;
  esac

  exit 0
fi
