#!/bin/bash
# TDD tests for the watchdog lock and selfheal HALT honouring.
#   1. Empty/corrupt lock timestamp is detected, logged and treated as stale (P3, retained).
#   2. A lock whose holder pid is ALIVE is never reclaimed, however old its timestamp
#      (f18337dc30c3: a live daemon was reclaimed after 300s and a second one started).
#   3. A lock whose holder pid is DEAD is reclaimed even with a fresh timestamp.
#   4. Two concurrent reclaimers of one dead-holder lock yield exactly one winner
#      (f18337dc30c3: rm -rf then mkdir let both reclaimers succeed).
#   5. selfheal.sh never spawns run-watchdog.sh while a HALT sentinel exists.
#
# HERMETIC: every fixture is a throwaway mktemp tree. CONDUCTOR_ROOT and
# AESOP_STATE_ROOT are pinned under the fixture so no live state is read or written.
# REPO_ROOT is used ONLY to locate the scripts under test. No daemon is started
# outside the fixture; no background process is left running.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RW="$REPO_ROOT/daemons/run-watchdog.sh"
SH="$REPO_ROOT/daemons/selfheal.sh"
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

PASSED=0
FAILED=0
pass() { echo "PASS: $*"; PASSED=$((PASSED + 1)); }
fail() { echo "FAIL: $*"; FAILED=$((FAILED + 1)); }

# A pid that is guaranteed dead: the pid of a bash process that has already exited.
dead_pid() { bash -c 'echo $$'; }

CYCLE_MOCK="$TMP_DIR/mock-cycle.sh"
printf '%s\n' '#!/bin/bash' 'COUNT=$(cat "$1" 2>/dev/null || echo 0)' 'echo $((COUNT + 1)) > "$1"' > "$CYCLE_MOCK"
chmod +x "$CYCLE_MOCK"

# run_once <aesop_root> <counter_file> <out_file>: one --once watchdog pass against a fixture.
run_once() {
  AESOP_ROOT="$1" \
    CONDUCTOR_ROOT="$TMP_DIR/conductor" \
    AESOP_STATE_ROOT="$1/state" \
    AESOP_WATCHDOG_CYCLE_CMD="$CYCLE_MOCK $2" \
    bash "$RW" --once > "$3" 2>&1
}

new_fixture() {
  local root="$1"
  mkdir -p "$root/state" "$TMP_DIR/conductor/state"
  echo 0 > "$root/counter"
}

echo "=== Test 1: empty lock timestamp (dead holder) is reclaimed and logged ==="
ROOT="$TMP_DIR/t1"; new_fixture "$ROOT"
LOCK="$ROOT/state/.watchdog-lock"; mkdir -p "$LOCK"
touch "$LOCK/timestamp"
echo "$(dead_pid)" > "$LOCK/pid"
run_once "$ROOT" "$ROOT/counter" "$ROOT/out.log"; rc=$?
if [ "$(cat "$ROOT/counter")" = "1" ] && grep -q "empty/corrupt lock timestamp" "$ROOT/out.log" && grep -q "reclaimed" "$ROOT/out.log"; then
  pass "empty timestamp + dead pid: cycle ran, corrupt-timestamp and reclaimed messages logged (exit $rc)"
else
  fail "empty timestamp + dead pid: expected cycle to run with reclaim messages (exit $rc, count $(cat "$ROOT/counter"))"; cat "$ROOT/out.log"
fi

echo "=== Test 1b: no pid file + empty timestamp is reclaimed and logged ==="
ROOT="$TMP_DIR/t1b"; new_fixture "$ROOT"
LOCK="$ROOT/state/.watchdog-lock"; mkdir -p "$LOCK"
touch "$LOCK/timestamp"
run_once "$ROOT" "$ROOT/counter" "$ROOT/out.log"; rc=$?
if [ "$(cat "$ROOT/counter")" = "1" ] && grep -q "empty/corrupt lock timestamp" "$ROOT/out.log"; then
  pass "no pid + empty timestamp: cycle ran and corrupt-timestamp message logged (exit $rc)"
else
  fail "no pid + empty timestamp: expected cycle to run (exit $rc, count $(cat "$ROOT/counter"))"; cat "$ROOT/out.log"
fi

echo "=== Test 2: live holder with a 1000s-old timestamp is NEVER reclaimed ==="
ROOT="$TMP_DIR/t2"; new_fixture "$ROOT"
LOCK="$ROOT/state/.watchdog-lock"; mkdir -p "$LOCK"
echo "$$" > "$LOCK/pid"                       # this test shell: alive for the whole run
echo $(( $(date +%s) - 1000 )) > "$LOCK/timestamp"
run_once "$ROOT" "$ROOT/counter" "$ROOT/out.log"; rc=$?
if [ "$(cat "$ROOT/counter")" = "0" ] && [ -d "$LOCK" ] && [ "$(cat "$LOCK/pid")" = "$$" ] && grep -q "already running" "$ROOT/out.log"; then
  pass "live pid + stale timestamp: no second cycle, lock untouched (exit $rc)"
else
  fail "live pid + stale timestamp: lock was reclaimed by a second daemon (exit $rc, count $(cat "$ROOT/counter"))"; cat "$ROOT/out.log"
fi

echo "=== Test 3: dead holder with a FRESH timestamp is reclaimed ==="
ROOT="$TMP_DIR/t3"; new_fixture "$ROOT"
LOCK="$ROOT/state/.watchdog-lock"; mkdir -p "$LOCK"
echo "$(dead_pid)" > "$LOCK/pid"
date +%s > "$LOCK/timestamp"
run_once "$ROOT" "$ROOT/counter" "$ROOT/out.log"; rc=$?
if [ "$(cat "$ROOT/counter")" = "1" ] && [ "$rc" = "0" ] && grep -q "reclaimed" "$ROOT/out.log"; then
  pass "dead pid + fresh timestamp: reclaimed and cycle ran (exit $rc)"
else
  fail "dead pid + fresh timestamp: expected reclaim and a cycle (exit $rc, count $(cat "$ROOT/counter"))"; cat "$ROOT/out.log"
fi

echo "=== Test 4: two concurrent reclaimers of one dead-holder lock -> exactly one winner ==="
# acquire_lock is driven directly from sourced run-watchdog.sh (its exec-guard keeps
# main() from running). Each driver holds the lock for 1s after acquiring it, so a
# loser that arrives while the winner is alive must see a live holder.
ROOT="$TMP_DIR/t4"; mkdir -p "$ROOT"
bad=0
for i in $(seq 1 15); do
  mkdir -p "$ROOT/$i"
  LOCK="$ROOT/$i/lock"; mkdir "$LOCK"
  echo "$(dead_pid)" > "$LOCK/pid"
  echo $(( $(date +%s) - 1000 )) > "$LOCK/timestamp"
  ( bash -c 'source "$1"; acquire_lock "$2" 300; echo "RC=$?"; sleep 1' _ "$RW" "$LOCK" > "$ROOT/$i/a.out" 2>/dev/null ) &
  ( bash -c 'source "$1"; acquire_lock "$2" 300; echo "RC=$?"; sleep 1' _ "$RW" "$LOCK" > "$ROOT/$i/b.out" 2>/dev/null ) &
  wait
  winners=$(grep -h '^RC=2$' "$ROOT/$i/a.out" "$ROOT/$i/b.out" 2>/dev/null | wc -l | tr -d ' ')
  if [ "$winners" != "1" ]; then
    bad=$((bad + 1))
    echo "  trial $i: winners=$winners"; cat "$ROOT/$i/a.out" "$ROOT/$i/b.out" 2>/dev/null
  fi
done
if [ "$bad" -eq 0 ]; then
  pass "15/15 concurrent reclaim trials produced exactly one winner (RC=2)"
else
  fail "$bad/15 concurrent reclaim trials did not produce exactly one winner"
fi

echo "=== Test 5: selfheal does not spawn run-watchdog.sh while HALT is set ==="
# Stale watchdog heartbeat + stub daemon. SELFHEAL.log records every spawn attempt
# synchronously ("RESTART watchdog: spawned"), so the log is the evidence.
for case in none per-checkout box-wide; do
  F="$TMP_DIR/sh-$case"; AR="$F/aesop"; CR="$F/conductor"
  mkdir -p "$AR/state" "$AR/daemons" "$CR/state"
  printf '%s\n' '#!/bin/bash' 'exit 0' > "$AR/daemons/run-watchdog.sh"
  echo $(( $(date +%s) - 5000 )) > "$CR/state/.watchdog-heartbeat"
  case "$case" in
    per-checkout) printf '{"reason":"test halt"}\n' > "$AR/state/.HALT" ;;
    box-wide)     printf '{"reason":"test halt"}\n' > "$CR/state/.HALT" ;;
  esac
  AESOP_ROOT="$AR" CONDUCTOR_ROOT="$CR" bash "$SH" --once > "$F/out.log" 2>&1
  spawns=$(grep -c 'RESTART watchdog: spawned' "$AR/state/SELFHEAL.log" 2>/dev/null)
  if [ "$case" = "none" ]; then
    if [ "$spawns" = "1" ]; then
      pass "control: stale heartbeat, no HALT -> watchdog spawn attempted"
    else
      fail "control: stale heartbeat with no HALT should spawn once (spawns=$spawns)"; cat "$AR/state/SELFHEAL.log"
    fi
  else
    if [ "$spawns" = "0" ] && grep -q 'HALT sentinel present' "$AR/state/SELFHEAL.log"; then
      pass "HALT ($case) present -> no watchdog spawn, skip logged"
    else
      fail "HALT ($case) present -> watchdog was spawned (spawns=$spawns)"; cat "$AR/state/SELFHEAL.log"
    fi
  fi
done

echo ""
echo "LOCKGUARD SUITE: $PASSED passed, $FAILED failed"
exit "$FAILED"
