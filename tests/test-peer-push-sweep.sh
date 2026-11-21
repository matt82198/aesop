#!/usr/bin/env bash
# TDD suite for tools/peer_push_sweep.py (watchdog peer push sweep) and its cycle wiring.
# HERMETIC: every repo, bare origin and state dir lives under one mktemp root. The sweep is
# always given explicit --repo / --config / --state-dir, and HOME points at the fixture so no
# real ~/.gitconfig or fleet repo is read or written.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SWEEP="$REPO_ROOT/tools/peer_push_sweep.py"
WATCHDOG="$REPO_ROOT/daemons/run-watchdog.sh"
PY="${PYTHON_EXE:-python}"

TMP_RAW="$(mktemp -d)"
# Native (Windows) git and python cannot read MSYS /tmp paths; convert when available.
if command -v cygpath >/dev/null 2>&1; then TMP="$(cygpath -m "$TMP_RAW")"; else TMP="$TMP_RAW"; fi
trap 'rm -rf "$TMP_RAW"' EXIT

export HOME="$TMP/home"
mkdir -p "$HOME"
export GIT_AUTHOR_NAME=peer-test GIT_AUTHOR_EMAIL=peer-test@example.invalid
export GIT_COMMITTER_NAME=peer-test GIT_COMMITTER_EMAIL=peer-test@example.invalid
export GIT_CONFIG_NOSYSTEM=1

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1"; }
# check <description> <command...>: pass when the command succeeds.
check() {
  local desc="$1"
  shift
  if "$@"; then pass "$desc"; else fail "$desc"; fi
}
# not <command...>: succeeds when the command fails.
not() {
  if "$@"; then return 1; fi
  return 0
}

# new_fixture <name>: bare origin plus repo "aesop" on main, with origin/main pushed.
new_fixture() {
  F="$TMP/$1"
  F_ORIGIN="$F/origin.git"
  F_REPO="$F/aesop"
  F_STATE="$F/state"
  F_LOG="$F_STATE/FLEET-BACKUP.log"
  mkdir -p "$F_STATE"
  git init -q --bare "$F_ORIGIN"
  git init -q -b main "$F_REPO"
  git -C "$F_REPO" commit -q --allow-empty -m "init"
  git -C "$F_REPO" remote add origin "$F_ORIGIN"
  git -C "$F_REPO" push -q origin main
}

# add_wt <branch> <dir-name> [committer-date]: worktree on a new branch with one unpushed commit.
add_wt() {
  local branch="$1" dir="$F/$2" date="${3:-}"
  git -C "$F_REPO" worktree add -q -b "$branch" "$dir" main
  printf '%s\n' "$branch" > "$dir/work-$2.txt"
  git -C "$dir" add -- "work-$2.txt"
  if [ -n "$date" ]; then
    GIT_COMMITTER_DATE="$date" git -C "$dir" commit -q -m "work $branch"
  else
    git -C "$dir" commit -q -m "work $branch"
  fi
}

# run_sweep [flags...]: one sweep invocation against the current fixture (config from $CFG if set).
run_sweep() {
  "$PY" "$SWEEP" --aesop-root "$F_REPO" --config "${CFG:-$F/no-config.json}" \
    --state-dir "$F_STATE" --log "$F_LOG" --repo "$F_REPO" "$@"
}

# wait_idle: block until the detached push worker has released the lock (about 60s max).
wait_idle() {
  local i
  for i in $(seq 1 300); do
    [ -e "$F_STATE/.peer-push.lock" ] || return 0
    sleep 0.2
  done
  return 1
}

# remote_has <branch>: origin has refs/heads/<branch>.
remote_has() {
  git -C "$F_ORIGIN" show-ref --verify -q "refs/heads/$1"
}

# log_has <extended-regex>: the sweep log has a matching line.
log_has() {
  grep -Eq -- "$1" "$F_LOG" 2>/dev/null
}

echo "=== T1: unpushed codex/x is pushed ==="
new_fixture t1
add_wt codex/x wt-x
run_sweep > "$F/out" 2>&1
rc=$?
wait_idle
check "T1 sweep exits 0" test "$rc" -eq 0
check "T1 codex/x reached origin" remote_has codex/x
check "T1 log records PUSHED for codex/x" log_has 'peer-push aesop codex/x [0-9a-f]{12} PUSHED'

echo "=== T2: main and detached HEAD are never pushed ==="
new_fixture t2
main_before="$(git -C "$F_ORIGIN" rev-parse main)"
git -C "$F_REPO" commit -q --allow-empty -m "local main ahead of origin"
git -C "$F_REPO" worktree add -q --detach "$F/wt-det" main
printf 'detached\n' > "$F/wt-det/detached.txt"
git -C "$F/wt-det" add -- detached.txt
git -C "$F/wt-det" commit -q -m "detached work"
run_sweep --pattern '*' > "$F/out" 2>&1
wait_idle
check "T2 origin main untouched" test "$(git -C "$F_ORIGIN" rev-parse main)" = "$main_before"
check "T2 no detached or other branch created on origin" \
  test "$(git -C "$F_ORIGIN" for-each-ref --format='%(refname)' refs/heads | wc -l | tr -d ' ')" -eq 1
check "T2 log has no PUSHED line for main" not log_has 'peer-push aesop main [0-9a-f]{12} PUSHED'

echo "=== T3: pre-push refusal is BLOCKED with its reason and not retried ==="
new_fixture t3
add_wt codex/h wt-h
printf '%s\n' '#!/bin/sh' "echo attempt >> '$F/hook-count'" \
  'echo "secret-scan BLOCKED: fixture refusal" >&2' 'exit 1' > "$F_REPO/.git/hooks/pre-push"
chmod +x "$F_REPO/.git/hooks/pre-push"
run_sweep > "$F/out" 2>&1
wait_idle
check "T3 refused branch is not on origin" not remote_has codex/h
check "T3 log records BLOCKED with the hook reason" \
  log_has 'peer-push aesop codex/h [0-9a-f]{12} BLOCKED .*secret-scan BLOCKED: fixture refusal'
check "T3 hook ran exactly once" test "$(grep -c attempt "$F/hook-count")" -eq 1
run_sweep > "$F/out2" 2>&1
wait_idle
check "T3 second cycle does not retry the same sha" test "$(grep -c attempt "$F/hook-count")" -eq 1
check "T3 second cycle logs the not-retried state" log_has 'codex/h .*not retried'

echo "=== T4: held lock gives IN-FLIGHT and nothing is pushed ==="
new_fixture t4
add_wt codex/l wt-l
printf '%s' '{"repo":"aesop","branch":"codex/other","sha":"abcdef123456","key":"x|codex/other","pid":1}' \
  > "$F_STATE/.peer-push.lock"
run_sweep > "$F/out" 2>&1
rc=$?
check "T4 sweep exits 0" test "$rc" -eq 0
check "T4 log records IN-FLIGHT push in flight" \
  log_has 'peer-push aesop codex/other abcdef123456 IN-FLIGHT push in flight'
check "T4 nothing pushed while in flight" not remote_has codex/l
check "T4 the held lock is left alone" test -e "$F_STATE/.peer-push.lock"

echo "=== T5: one push per cycle, oldest unpushed first ==="
new_fixture t5
add_wt codex/new wt-new "2026-01-02T00:00:00Z"
add_wt codex/old wt-old "2026-01-01T00:00:00Z"
run_sweep > "$F/out" 2>&1
wait_idle
check "T5 cycle 1 pushed exactly one codex branch" \
  test "$(git -C "$F_ORIGIN" for-each-ref --format='%(refname)' refs/heads | grep -c codex/)" -eq 1
check "T5 cycle 1 pushed the oldest branch" remote_has codex/old
run_sweep > "$F/out2" 2>&1
wait_idle
check "T5 cycle 2 pushed the next branch" remote_has codex/new

echo "=== T6: dirty worktree is reported, never committed ==="
new_fixture t6
add_wt codex/d wt-d
printf 'scratch\n' > "$F/wt-d/scratch.txt"
git -C "$F/wt-d" worktree list >/dev/null 2>&1
d_head_before="$(git -C "$F/wt-d" rev-parse HEAD)"
git -C "$F_REPO" worktree add -q -b codex/p "$F/wt-p2" main
printf 'scratch\n' > "$F/wt-p2/scratch.txt"
run_sweep > "$F/out" 2>&1
wait_idle
check "T6 DIRTY line reported for the dirty worktree" \
  log_has 'peer-push aesop codex/p [0-9a-f]{12} DIRTY dirty, not committed'
check "T6 dirty file is not committed" \
  test -z "$(git -C "$F/wt-p2" log --oneline -- scratch.txt)"
check "T6 dirty file is still uncommitted" \
  test -n "$(git -C "$F/wt-p2" status --porcelain -- scratch.txt)"
check "T6 HEAD of dirty branch unchanged by sweep" \
  test "$(git -C "$F/wt-d" rev-parse HEAD)" = "$d_head_before"

echo "=== T7: non-matching branch is not pushed (anti-vacuity: drop the pattern filter and this goes red) ==="
new_fixture t7
add_wt feature/y wt-y
run_sweep > "$F/out" 2>&1
wait_idle
check "T7 feature/y not pushed under default patterns" not remote_has feature/y
check "T7 no log line for feature/y" not log_has 'peer-push aesop feature/y'

echo "=== T8: branch patterns come from aesop.config.json peer_push ==="
new_fixture t8
add_wt codex/c wt-c
add_wt agy/a wt-a
printf '%s\n' '{"peer_push": {"branch_patterns": ["agy/*"]}}' > "$F/cfg.json"
CFG="$F/cfg.json" run_sweep > "$F/out" 2>&1
wait_idle
check "T8 agy/a pushed under agy-only config" remote_has agy/a
check "T8 codex/c not pushed under agy-only config" not remote_has codex/c

echo "=== T9: dry-run lists the push and writes nothing ==="
new_fixture t9
add_wt codex/z wt-z
run_sweep --dry-run > "$F/dry.out" 2>&1
rc=$?
check "T9 dry-run exits 0" test "$rc" -eq 0
check "T9 dry-run names the would-push" grep -Fq "DRY-RUN would-push aesop codex/z" "$F/dry.out"
check "T9 dry-run pushes nothing" not remote_has codex/z
check "T9 dry-run writes no lock, state or log" \
  test ! -e "$F_STATE/.peer-push.lock" -a ! -e "$F_STATE/.peer-push-state.json" -a ! -e "$F_LOG"

echo "=== T10: wiring and forbidden flags ==="
check "T10 sweep script exists" test -f "$SWEEP"
check "T10 run-watchdog wires run_peer_push_sweep into --once and daemon cycles" \
  test "$(grep -c 'run_peer_push_sweep' "$WATCHDOG")" -ge 3
check "T10 sweep never passes --force, -f or --no-verify" \
  not grep -Eq -- "[\"'](--force|-f|--no-verify)[\"']" "$SWEEP"

echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
