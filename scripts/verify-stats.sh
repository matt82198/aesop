#!/bin/bash
#
# verify-stats.sh — check or regenerate Aesop's self-reported stats
#
# SINGLE SOURCE OF TRUTH: stats.json in the repo root.
#
# All stats in README.md, docs/, and dashboards consume stats.json.
# This script ensures stats.json stays current and consistent with git reality.
#
# Implementation: tools/self_stats.py uses:
#   - git rev-list --count HEAD            -> total commit count
#   - gh API (repo:<origin> is:pr is:merged), fallback distinct PR numbers from
#     commit subjects (merge + squash patterns) -> merged PR count, source recorded
#   - git log --reverse --format=%cI       -> project age
#   - git log --format=%an|%ae + Co-Authored-By trailers -> classified authors
#   - git ls-files                         -> files tracked
#
# --check enforces, beyond README<->stats.json consistency:
#   * internal consistency: no two PR counts in stats.json may disagree
#   * honest economics: no 0.0-but-present token/cost filler fields
#   * provenance: git.merged_prs must carry a source ('gh-api' | 'git-log')
#   * freshness: stats.json must not lag HEAD by generated_at age or commit count
#     past practical thresholds (regenerate hint emitted on failure)
#
# Usage:
#   bash scripts/verify-stats.sh          # Check (README match + integrity + freshness)
#   bash scripts/verify-stats.sh --check  # Explicit check mode (exit 0 = pass, exit 1 = fail)
#   bash scripts/verify-stats.sh --regenerate  # Regenerate stats.json from live git
#

set -eu

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATS_FILE="${REPO_ROOT}/stats.json"
README_FILE="${REPO_ROOT}/README.md"

# Ensure UTF-8 output encoding on Windows cp1252 systems
# Fixes: arrow chars (U+2192) failing on Windows cp1252 locale
export PYTHONIOENCODING=utf-8

# Determine mode
MODE="${1:-check}"

case "$MODE" in
  --check|check)
    # Exit 0 if README metrics match stats.json; exit 1 if drift detected
    python "${REPO_ROOT}/tools/self_stats.py" \
      --check \
      --repo "${REPO_ROOT}" \
      --stats-file "${STATS_FILE}" \
      --readme "${README_FILE}"
    ;;
  --regenerate|regenerate)
    # Regenerate stats.json from live git state
    echo "[verify-stats] Regenerating stats.json from git..." >&2
    python "${REPO_ROOT}/tools/self_stats.py" \
      --regenerate \
      --repo "${REPO_ROOT}" \
      --stats-file "${STATS_FILE}"
    echo "[verify-stats] Stats regenerated. Run 'git diff stats.json' to review." >&2
    ;;
  *)
    echo "Usage: bash scripts/verify-stats.sh [--check|--regenerate]"
    echo ""
    echo "  --check       Check if README metrics match stats.json (default)"
    echo "  --regenerate  Regenerate stats.json from live git"
    echo ""
    echo "Single source of truth: $STATS_FILE"
    exit 1
    ;;
esac
