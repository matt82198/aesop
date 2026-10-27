#!/usr/bin/env bash
set -uo pipefail

# Red-proof suite for the secret-gate bypasses in daemons/backup-fleet.sh
# (audit rows SEM-3, SEM-4, SEM-6, SEM-7).
#
# Every test drives the REAL process_repo() (sourced from the script; main() is
# guarded by BASH_SOURCE) against a throwaway git repo with a bare local origin.
# No network remote, no real repo, nothing under ~.
#
# Expected status on the CURRENT code:
#   control  PASS  (the scanner fixture really flags the planted secret, so a red
#                   below is the backup gate's fault, not a silent scanner)
#   A        FAIL  root-commit secret in a no-upstream repo is not BLOCKED (SEM-4)
#   B        FAIL  WIP snapshot pushes an unpushed commit carrying the secret (SEM-3)
#   C        FAIL  BLOCKED verdict lost when a WARN line shares the NUL record (SEM-6)
#   D        FAIL  empty COMMIT pushes a delete refspec and drops today's backup (SEM-7)
#
# The secret is assembled at runtime (never a literal in source).
# HERMETIC: every path lives under a mktemp dir; git runs with an empty global config.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# AESOP_TEST_BACKUP_FLEET_SCRIPT lets a caller point the suite at another copy of the
# daemon by literal path (e.g. a sibling worktree) without editing this file.
BACKUP_FLEET_SCRIPT="${AESOP_TEST_BACKUP_FLEET_SCRIPT:-$SCRIPT_DIR/daemons/backup-fleet.sh}"
SECRET_SCAN_SRC="$SCRIPT_DIR/tools/secret_scan.py"

TEST_DIR="$(mktemp -d)"
if command -v cygpath >/dev/null 2>&1; then
  TEST_DIR="$(cygpath -m "$TEST_DIR")"
fi
PASSED=0
FAILED=0
WIP_DATE="$(date +%Y%m%d)"
WIP_REF="refs/heads/backup/wip-$WIP_DATE"

cleanup() {
  rm -rf "$TEST_DIR" 2>/dev/null || true
}
trap cleanup EXIT

export GIT_CONFIG_GLOBAL="$TEST_DIR/empty-gitconfig"
export GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME="Aesop Test" GIT_AUTHOR_EMAIL="test@example.invalid"
export GIT_COMMITTER_NAME="Aesop Test" GIT_COMMITTER_EMAIL="test@example.invalid"
: > "$GIT_CONFIG_GLOBAL"

# Runtime-assembled dummy AWS-shaped key (never a literal in source).
SECRET="AKIA""IOSFODNN7EXAMPLE"

# Absolute git binary for the shim (resolved before any PATH change).
REAL_GIT="$(command -v git)"

log()  { echo "[TEST] $*"; }
fail() { echo "FAIL: $*" >&2; FAILED=$((FAILED + 1)); }
pass() { echo "PASS: $*"; PASSED=$((PASSED + 1)); }

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

# $fx/aesop    AESOP_ROOT with the real scanner copied in
# $fx/conductor explicit CONDUCTOR_ROOT
# $fx/origin.git bare local origin (never a network remote)
# $fx/repo     the repo under test, HEAD on main, origin configured, NO upstream yet
new_fixture() {
  local fx="$1"
  rm -rf "$fx"
  mkdir -p "$fx/aesop/state" "$fx/aesop/tools" "$fx/conductor/state"
  cp "$SECRET_SCAN_SRC" "$fx/aesop/tools/secret_scan.py"
  git init -q --bare "$fx/origin.git"
  git init -q "$fx/repo"
  git -C "$fx/repo" symbolic-ref HEAD refs/heads/main
  git -C "$fx/repo" remote add origin "$fx/origin.git"
}

# commit_file FX NAME CONTENT -- write and commit one file on the fixture repo.
commit_file() {
  local fx="$1" name="$2" content="$3"
  printf '%s\n' "$content" > "$fx/repo/$name"
  git -C "$fx/repo" add -- "$name"
  git -C "$fx/repo" commit -q -m "add $name"
}

# origin_has_secret ORIGIN_GIT_DIR -- succeeds if ANY ref on origin contains the secret.
origin_has_secret() {
  local origin="$1" ref
  while IFS= read -r ref; do
    if git --git-dir="$origin" grep -q -F -e "$SECRET" "$ref" -- 2>/dev/null; then
      return 0
    fi
  done < <(git --git-dir="$origin" for-each-ref --format='%(refname)')
  return 1
}

# run_process_repo FX REPO [ENV...] -- drive the real process_repo() exactly as
# the daemon does: stdout is the NUL-protocol record file, stderr is kept aside.
run_process_repo() {
  local fx="$1" repo="$2"
  shift 2
  env -u CONDUCTOR_ROOT AESOP_ROOT="$fx/aesop" CONDUCTOR_ROOT="$fx/conductor" "$@" \
    bash -c 'source "$1"; process_repo "$2"' _ "$BACKUP_FLEET_SCRIPT" "$repo" \
    > "$fx/record.out" 2> "$fx/stderr.out"
}

# verdicts FX -- every verdict word process_repo printed, space separated.
verdicts() {
  grep -aEo 'BLOCKED|PUSHED|SNAPSHOTTED|CLEAN|FAILED' "$1/record.out" | paste -sd' ' -
}

# origin_history_has_secret ORIGIN_GIT_DIR -- succeeds if ANY commit in ANY ref's
# history added or removed the secret (pickaxe). Catches add-then-delete leaks
# that the tip tree no longer shows.
origin_history_has_secret() {
  [ -n "$(git --git-dir="$1" log --all -S"$SECRET" --format=%H 2>/dev/null)" ]
}

# posix_path P -- PATH is colon-separated, so a Windows drive letter cannot go in it.
posix_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"; else printf '%s' "$1"; fi
}

# make_shim DIR MODE -- write a git shim named "git" into DIR that fails on purpose
# and passes everything else through to the real git (absolute path, no PATH lookup).
#   MODE=listing    exit 128 for any -z invocation of ls-files or diff (tracked/untracked listings)
#   MODE=write-tree exit 128 for write-tree
make_shim() {
  local dir="$1" mode="$2"
  mkdir -p "$dir"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'REAL_GIT=%q\n' "$REAL_GIT"
    printf 'MODE=%q\n' "$mode"
    cat <<'SHIM'
z=0; sub=0; wt=0
for a in "$@"; do
  case "$a" in
    -z) z=1 ;;
    ls-files|diff) sub=1 ;;
    write-tree) wt=1 ;;
  esac
done
if [ "$MODE" = listing ] && [ "$z" -eq 1 ] && [ "$sub" -eq 1 ]; then exit 128; fi
if [ "$MODE" = write-tree ] && [ "$wt" -eq 1 ]; then exit 128; fi
exec "$REAL_GIT" "$@"
SHIM
  } > "$dir/git"
  chmod +x "$dir/git"
}

# ---------------------------------------------------------------------------
# Control: the scanner fixture flags the planted secret in a range scan.
# If this were red, every red below would be meaningless.
# ---------------------------------------------------------------------------
test_control_scanner_flags_planted_secret() {
  log "CONTROL: real secret_scan.py flags the planted secret in a range"
  local fx="$TEST_DIR/tcontrol"
  new_fixture "$fx"
  commit_file "$fx" README.txt "seed"
  commit_file "$fx" leak.txt "aws_key = $SECRET"
  local py=python3
  command -v python3 >/dev/null 2>&1 || py=python
  "$py" "$fx/aesop/tools/secret_scan.py" --range HEAD~1..HEAD --repo "$fx/repo" > "$fx/scan.out" 2>&1
  local rc=$?
  if [ "$rc" -ne 1 ]; then
    fail "control: scanner expected rc=1 on planted secret, got $rc"; cat "$fx/scan.out" >&2; return 1
  fi
  pass "control: scanner rc=1 on planted secret in range"
}

# ---------------------------------------------------------------------------
# A (SEM-4): a secret in the ROOT commit of a no-upstream repo must be BLOCKED.
# Two commits, no upstream. The root holds the secret; HEAD is innocent.
# The fallback range is base..HEAD where base=HEAD~1=root, so the root is never scanned.
# ---------------------------------------------------------------------------
test_a_root_commit_secret_no_upstream_blocked() {
  log "TEST A: root-commit secret in a no-upstream repo is BLOCKED, never pushed"
  local fx="$TEST_DIR/ta"
  new_fixture "$fx"
  commit_file "$fx" leak.txt "aws_key = $SECRET"
  commit_file "$fx" notes.txt "innocent second commit"

  run_process_repo "$fx" "$fx/repo"

  local got
  got="$(verdicts "$fx")"
  if [[ " $got " != *" BLOCKED "* ]]; then
    fail "root-commit secret not BLOCKED; verdicts=[${got:-none}] (expected BLOCKED)"
    return 1
  fi
  if origin_has_secret "$fx/origin.git"; then
    fail "root-commit secret reached origin; verdicts=[$got]"
    return 1
  fi
  pass "A: root-commit secret BLOCKED, never reached origin"
}

# ---------------------------------------------------------------------------
# B (SEM-3): dirty tree + unpushed commit carrying the secret. The WIP snapshot
# parent is local HEAD, so the unpushed commit rides along. Only the working-tree
# files are scanned, so the secret is not seen. It must not reach origin.
# ---------------------------------------------------------------------------
test_b_wip_snapshot_does_not_push_unpushed_secret() {
  log "TEST B: WIP snapshot must not push an unpushed commit carrying the secret"
  local fx="$TEST_DIR/tb"
  new_fixture "$fx"
  commit_file "$fx" README.txt "seed"
  git -C "$fx/repo" push -q -u origin main
  commit_file "$fx" leak.txt "aws_key = $SECRET"          # unpushed, carries the secret
  printf 'dirty edit\n' >> "$fx/repo/README.txt"          # uncommitted and clean -> WIP path

  run_process_repo "$fx" "$fx/repo"

  local got
  got="$(verdicts "$fx")"
  if origin_has_secret "$fx/origin.git"; then
    fail "WIP snapshot pushed the unpushed secret to origin; verdicts=[$got] (expected BLOCKED, no push)"
    return 1
  fi
  pass "B: WIP snapshot did not push the unpushed secret to origin"
}

# ---------------------------------------------------------------------------
# C (SEM-6): a BLOCKED verdict must survive a WARN line. process_repo's log()
# writes to stdout, which is the NUL record, so the WARN lands in record 1.
# The parse below is the same awk the daemon's main loop uses.
# Secret sits in an unpushed NON-root commit so SEM-4 cannot hide it.
# ---------------------------------------------------------------------------
test_c_blocked_verdict_survives_warn_line() {
  log "TEST C: BLOCKED verdict is read correctly when a WARN line was logged"
  local fx="$TEST_DIR/tc"
  new_fixture "$fx"
  commit_file "$fx" README.txt "seed"
  commit_file "$fx" leak.txt "aws_key = $SECRET"          # no upstream; secret in unpushed commit

  run_process_repo "$fx" "$fx/repo"

  local state
  state=$(awk 'BEGIN{RS="\0"} NR==1 {print}' "$fx/record.out")
  if [ "$state" != "BLOCKED" ]; then
    fail "daemon's record parse reads state as [$(printf '%q' "$state")], expected BLOCKED"
    return 1
  fi
  pass "C: record state parses as BLOCKED with a WARN line logged"
}

# ---------------------------------------------------------------------------
# D (SEM-7a): when git commit-tree fails, COMMIT is empty. The push then uses the
# refspec ":refs/heads/backup/wip-DATE", a DELETE of today's backup on origin.
# Force a commit-tree failure with user.useConfigOnly and no identity. The
# test must show origin's existing backup ref is untouched and nothing is pushed.
# ---------------------------------------------------------------------------
test_d_empty_commit_never_pushes() {
  log "TEST D: an empty COMMIT (commit-tree failure) never produces a push"
  local fx="$TEST_DIR/td"
  new_fixture "$fx"
  commit_file "$fx" README.txt "seed"
  git -C "$fx/repo" push -q -u origin main
  git -C "$fx/repo" push -q origin "HEAD:$WIP_REF"        # today's backup already on origin
  local before after
  before=$(git --git-dir="$fx/origin.git" rev-parse -q --verify "$WIP_REF" 2>/dev/null || echo "")
  if [ -z "$before" ]; then
    fail "fixture error: origin $WIP_REF was not seeded"; return 1
  fi
  printf 'dirty edit\n' >> "$fx/repo/README.txt"          # uncommitted -> WIP path

  # Break commit-tree deterministically: empty ident values make git refuse
  # ("empty ident name not allowed"). Precondition below proves it really fails.
  local broken_ident=(
    GIT_AUTHOR_NAME= GIT_AUTHOR_EMAIL= GIT_COMMITTER_NAME= GIT_COMMITTER_EMAIL=
  )
  if env "${broken_ident[@]}" git -C "$fx/repo" commit-tree 'HEAD^{tree}' -m precondition >/dev/null 2>&1; then
    fail "fixture error: commit-tree still succeeds with broken identity, test would be vacuous"
    return 1
  fi

  run_process_repo "$fx" "$fx/repo" "${broken_ident[@]}"
  if [ -z "$(verdicts "$fx")" ]; then
    fail "fixture error: process_repo produced no verdict; script never ran"; cat "$fx/stderr.out" >&2
    return 1
  fi

  after=$(git --git-dir="$fx/origin.git" rev-parse -q --verify "$WIP_REF" 2>/dev/null || echo "")
  local got
  got="$(verdicts "$fx")"
  if [ -z "$after" ]; then
    fail "empty COMMIT deleted origin $WIP_REF (delete refspec pushed); verdicts=[$got]"
    return 1
  fi
  if [ "$after" != "$before" ]; then
    fail "empty COMMIT moved origin $WIP_REF from $before to $after; verdicts=[$got]"
    return 1
  fi
  if [[ " $got " == *" SNAPSHOTTED "* ]]; then
    fail "empty COMMIT reported SNAPSHOTTED; verdicts=[$got]"
    return 1
  fi
  pass "D: empty COMMIT produced no push; origin $WIP_REF untouched"
}

# ---------------------------------------------------------------------------
# E: a clean repo whose origin is unreachable must report FAILED. An unpushed
# commit exists (so the push path runs) and the origin path does not exist, so
# the push fails. Reporting CLEAN, SNAPSHOTTED or PUSHED hides the failed backup.
# Origin is a local path that does not exist, never a network URL.
# ---------------------------------------------------------------------------
test_e_unreachable_origin_reports_failed() {
  log "TEST E: clean repo with an unreachable origin must give FAILED, never CLEAN/SNAPSHOTTED/PUSHED"
  local fx="$TEST_DIR/te"
  new_fixture "$fx"
  commit_file "$fx" README.txt "seed"
  git -C "$fx/repo" push -q -u origin main
  git -C "$fx/repo" remote set-url origin "$fx/no-such-origin.git"   # local path, does not exist
  commit_file "$fx" notes.txt "unpushed but clean"                   # clean tree, unpushed commit

  run_process_repo "$fx" "$fx/repo"

  local got
  got="$(verdicts "$fx")"
  if [ -z "$got" ]; then
    fail "fixture error: process_repo produced no verdict; script never ran"; cat "$fx/stderr.out" >&2
    return 1
  fi
  if [[ " $got " == *" CLEAN "* || " $got " == *" SNAPSHOTTED "* || " $got " == *" PUSHED "* ]]; then
    fail "unreachable origin reported a success verdict; verdicts=[$got] (expected FAILED)"
    return 1
  fi
  if [[ " $got " != *" FAILED "* ]]; then
    fail "unreachable origin gave no FAILED verdict; verdicts=[$got] (expected FAILED)"
    return 1
  fi
  pass "E: unreachable origin reports FAILED, no success verdict"
}

# ---------------------------------------------------------------------------
# F (S5 R1): upstream present, clean tree. A secret is committed in one unpushed
# commit and git rm'd in a LATER unpushed commit. The net diff of the range is
# empty, so a diff-name scan sees nothing and the branch is pushed with the
# secret in its history. The verdict must be BLOCKED and the secret must not
# appear in any origin history.
# ---------------------------------------------------------------------------
test_f_add_then_remove_secret_in_unpushed_commits_blocked() {
  log "TEST F: secret added then removed across unpushed commits must be BLOCKED, never in origin history"
  local fx="$TEST_DIR/tf"
  new_fixture "$fx"
  commit_file "$fx" README.txt "seed"
  git -C "$fx/repo" push -q -u origin main
  commit_file "$fx" leak.txt "aws_key = $SECRET"          # unpushed commit adds the secret
  git -C "$fx/repo" rm -q -- leak.txt                     # later unpushed commit removes it
  git -C "$fx/repo" commit -q -m "remove leak"

  run_process_repo "$fx" "$fx/repo"

  local got
  got="$(verdicts "$fx")"
  if origin_history_has_secret "$fx/origin.git"; then
    fail "secret reached origin history (added then removed); verdicts=[$got] (expected BLOCKED)"
    return 1
  fi
  if [[ " $got " != *" BLOCKED "* ]]; then
    fail "add-then-remove secret not BLOCKED; verdicts=[${got:-none}] (expected BLOCKED)"
    return 1
  fi
  pass "F: add-then-remove secret BLOCKED, absent from origin history"
}

# ---------------------------------------------------------------------------
# G (S5 R3b): every -z listing (ls-files / diff) fails, so the tracked-file scan
# gets an empty list and passes. A modified tracked file holds the secret. The
# verdict must be BLOCKED or FAILED, never SNAPSHOTTED, and nothing is pushed.
# A precondition proves the shim really makes the listing fail.
# ---------------------------------------------------------------------------
test_g_listing_failure_never_snapshots() {
  log "TEST G: failed tracked/untracked listings must give BLOCKED or FAILED, never SNAPSHOTTED, and push nothing"
  local fx="$TEST_DIR/tg"
  new_fixture "$fx"
  commit_file "$fx" README.txt "seed"
  git -C "$fx/repo" push -q -u origin main
  printf 'aws_key = %s\n' "$SECRET" >> "$fx/repo/README.txt"   # modified tracked file holds the secret
  make_shim "$fx/shim" listing
  local shim_path
  shim_path="$(posix_path "$fx/shim")"
  if env PATH="$shim_path:$PATH" git -C "$fx/repo" -c core.quotepath=off ls-files -z -m >/dev/null 2>&1; then
    fail "fixture error: listing shim did not fail on ls-files -z; test would be vacuous"
    return 1
  fi

  run_process_repo "$fx" "$fx/repo" PATH="$shim_path:$PATH"

  local got refs
  got="$(verdicts "$fx")"
  refs="$(git --git-dir="$fx/origin.git" for-each-ref --format='%(refname)' refs/heads/backup/)"
  if [ -n "$refs" ]; then
    fail "listing failure still pushed a backup ref: $refs; verdicts=[$got]"
    return 1
  fi
  if origin_has_secret "$fx/origin.git"; then
    fail "listing failure pushed the secret to origin; verdicts=[$got]"
    return 1
  fi
  if [[ " $got " == *" SNAPSHOTTED "* || " $got " == *" PUSHED "* || " $got " == *" CLEAN "* ]]; then
    fail "listing failure reported a success verdict; verdicts=[$got] (expected BLOCKED or FAILED)"
    return 1
  fi
  if [[ " $got " != *" BLOCKED "* && " $got " != *" FAILED "* ]]; then
    fail "listing failure gave no BLOCKED/FAILED verdict; verdicts=[${got:-none}]"
    return 1
  fi
  pass "G: listing failure gave $got, nothing pushed"
}

# ---------------------------------------------------------------------------
# H (S5 R-write-tree): git write-tree fails while there are uncommitted changes.
# The WIP path is skipped, and the clean-looking remainder must not report CLEAN.
# The verdict must be FAILED. A precondition proves the shim fails write-tree.
# ---------------------------------------------------------------------------
test_h_write_tree_failure_never_clean() {
  log "TEST H: write-tree failure with uncommitted changes must give FAILED, never CLEAN"
  local fx="$TEST_DIR/th"
  new_fixture "$fx"
  commit_file "$fx" README.txt "seed"
  git -C "$fx/repo" push -q -u origin main
  printf 'uncommitted edit\n' >> "$fx/repo/README.txt"
  make_shim "$fx/shim" write-tree
  local shim_path
  shim_path="$(posix_path "$fx/shim")"
  if env PATH="$shim_path:$PATH" git -C "$fx/repo" write-tree >/dev/null 2>&1; then
    fail "fixture error: write-tree shim did not fail; test would be vacuous"
    return 1
  fi

  run_process_repo "$fx" "$fx/repo" PATH="$shim_path:$PATH"

  local got refs
  got="$(verdicts "$fx")"
  refs="$(git --git-dir="$fx/origin.git" for-each-ref --format='%(refname)' refs/heads/backup/)"
  if [ -n "$refs" ]; then
    fail "write-tree failure still pushed a backup ref: $refs; verdicts=[$got]"
    return 1
  fi
  if [[ " $got " == *" CLEAN "* ]]; then
    fail "write-tree failure reported CLEAN with uncommitted changes; verdicts=[$got] (expected FAILED)"
    return 1
  fi
  if [[ " $got " != *" FAILED "* ]]; then
    fail "write-tree failure gave no FAILED verdict; verdicts=[${got:-none}] (expected FAILED)"
    return 1
  fi
  pass "H: write-tree failure gave FAILED, nothing pushed"
}

test_control_scanner_flags_planted_secret
test_a_root_commit_secret_no_upstream_blocked
test_b_wip_snapshot_does_not_push_unpushed_secret
test_c_blocked_verdict_survives_warn_line
test_d_empty_commit_never_pushes
test_e_unreachable_origin_reports_failed
test_f_add_then_remove_secret_in_unpushed_commits_blocked
test_g_listing_failure_never_snapshots
test_h_write_tree_failure_never_clean

echo ""
echo "Test Results: $PASSED passed, $FAILED failed"
if [ "$FAILED" -gt 0 ]; then
  exit 1
fi
