#!/usr/bin/env bash
# Blocks commits in the primary tree while a wave is in flight (state/.wave-in-flight marker); worktree commits pass.
set -uo pipefail

main() {
  # Resolve the marker relative to the CURRENT working tree, NOT a hardcoded primary path.
  # This is load-bearing: the marker (state/.wave-in-flight) is git-ignored, so a sibling
  # worktree checked out during a wave does NOT carry it — only the PRIMARY tree (where the
  # orchestrator sets it) does. Checking the current tree's own toplevel means primary-tree
  # commits are blocked during a wave while legitimate worktree-agent commits pass. A prior
  # version hardcoded ${AESOP_ROOT:-$HOME/aesop}, which resolved to the primary tree from every
  # worktree and thus blocked the entire fleet mid-wave (wave-24 incident). Do not reintroduce.
  local toplevel
  toplevel=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
  local marker_file="$toplevel/state/.wave-in-flight"

  if [ -f "$marker_file" ]; then
    printf 'Error: Wave in flight in this tree (%s). Commit from a sibling worktree, or clear the marker to override.\n' "$marker_file" >&2
    exit 1
  fi

  exit 0
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
