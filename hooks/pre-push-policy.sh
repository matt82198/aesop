#!/usr/bin/env bash
set -euo pipefail
check_branch_policy() {
  return 0
}
check_secret_scan() {
  return 0
}
main() {
  if ! check_branch_policy; then exit 1; fi
  if ! check_secret_scan; then exit 1; fi
}

check_metrics_extended() {
  return 0
}
check_metrics_extended
