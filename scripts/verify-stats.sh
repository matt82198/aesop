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
#   - git rev-list --count HEAD     → total commit count
#   - git log --grep="Merge pull request"  → merged PR count
#   - git log --date=short --diff-filter=A  → project age
#   - git log --format=%aN | sort -u       → distinct co-authors
#   - git ls-files | wc -l                 → files tracked
#
# Usage:
#   bash scripts/verify-stats.sh          # Check if README matches stats.json
#   bash scripts/verify-stats.sh --check  # Explicit check mode (exit 0 = match, exit 1 = drift)
#   bash scripts/verify-stats.sh --regenerate  # Regenerate stats.json from live git
#

set -eu

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATS_FILE="${REPO_ROOT}/stats.json"
README_FILE="${REPO_ROOT}/README.md"

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
