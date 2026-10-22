#!/bin/sh
# events_append_only.sh - pre-commit guard: events/*.jsonl is append-only.
# Rejects a commit that deletes, rewrites, renames or removes any existing event line.
# Only added files and appended lines pass. Fails closed if git cannot report the diff.
#
# Install: tools/event_store/init.py copies this script into a per-store hooks dir and points
# the store worktree's core.hooksPath there. Manual install: copy it to the hooks dir as pre-commit.
# If a pre-commit hook already exists, call this script from it instead of overwriting.

set -u

status_out=$(git diff --cached --name-status -M -- 'events/*.jsonl') || {
  echo "events-append-only: REJECTED: git could not report the staged diff" >&2
  exit 1
}
# --text: events/.gitattributes marks *.jsonl -text (binary), which makes numstat print "-".
numstat_out=$(git diff --cached --text --numstat -- 'events/*.jsonl') || {
  echo "events-append-only: REJECTED: git could not report the staged line counts" >&2
  exit 1
}

# A (added) and M (modified, judged by the line counts below) pass; D, R, C, T etc. are refused.
bad_status=$(printf '%s\n' "$status_out" | awk -F'\t' 'NF >= 2 && $1 !~ /^[AM]/ { print "  " $0 }')
bad_lines=$(printf '%s\n' "$numstat_out" | awk -F'\t' 'NF >= 3 && ($2 == "-" || $2 + 0 > 0) { print "  " $2 " line(s) removed or rewritten in " $3 }')

if [ -n "$bad_status" ] || [ -n "$bad_lines" ]; then
  echo "events-append-only: REJECTED. events/*.jsonl is append-only. Staged changes that delete or rewrite existing lines:" >&2
  [ -n "$bad_status" ] && printf '%s\n' "$bad_status" >&2
  [ -n "$bad_lines" ] && printf '%s\n' "$bad_lines" >&2
  echo "Fix: unstage and restore the file, then record the new fact with python -I tools/event_store/cli.py append." >&2
  exit 1
fi
exit 0
