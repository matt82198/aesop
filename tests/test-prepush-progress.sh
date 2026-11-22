#!/usr/bin/env bash
# Hermetic temp-repo tests for hooks/pre-push-policy.sh progress, immediate BLOCKED
# reporting, per-shard live progress, and installed .git/hooks/pre-push sync check.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_SCRIPT="$REPO_ROOT/hooks/pre-push-policy.sh"
EMIT_SCRIPT="$REPO_ROOT/tools/emit_receipt.py"

TMP_BASE="$(mktemp -d "${TMPDIR:-/tmp}/prepush-progress-test.XXXXXX")"
trap 'rm -rf "$TMP_BASE"' EXIT

cd "$TMP_BASE" || exit 1
source "$HOOK_SCRIPT"

py_bin="python3"
command -v python3 >/dev/null 2>&1 || py_bin="python"

passed=0
failed=0

record_result() {
  if [ "$1" -eq 0 ]; then
    passed=$((passed + 1))
  else
    failed=$((failed + 1))
  fi
}

setup_temp_push_repo() {
  local dir="$1"
  rm -rf "$dir" "$dir.base"
  mkdir -p "$dir/hooks" "$dir/tools" "$dir/state"
  cp "$HOOK_SCRIPT" "$dir/hooks/pre-push-policy.sh"
  cp "$REPO_ROOT/tools/receipt_common.py" "$dir/tools/receipt_common.py"
  cp "$REPO_ROOT/tools/ci_shard_runner.py" "$dir/tools/ci_shard_runner.py"
  cp "$REPO_ROOT/tools/linux_shape_check.py" "$dir/tools/linux_shape_check.py"
  cp "$EMIT_SCRIPT" "$dir/tools/emit_receipt.py"
  printf '{"violations": {}}\n' > "$dir/.subprocess-guard-baseline.json"
  printf '{}\n' > "$dir/.pyflakes-baseline.json"
  : > "$dir/tools/INDEX.md"
  printf '#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n' > "$dir/tools/secret_scan.py"
  chmod +x "$dir/tools/secret_scan.py"

  local stub
  for stub in $("$py_bin" "$REPO_ROOT/tools/gate_stub_list.py" "$HOOK_SCRIPT" | tr -d '\r'); do
    case "$stub" in
      emit_receipt|receipt_common|ci_shard_runner|linux_shape_check) continue ;;
    esac
    printf '#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n' > "$dir/tools/$stub.py"
    chmod +x "$dir/tools/$stub.py"
  done

  git -C "$dir" init -q -b main
  git -C "$dir" config user.email "test@example.com"
  git -C "$dir" config user.name "Test"
  git -C "$dir" config core.autocrlf false
  printf 'seed\n' > "$dir/README.md"
  git -C "$dir" add -A
  git -C "$dir" update-index --chmod=+x tools/secret_scan.py
  git -C "$dir" commit -q -m "base"

  cp "$HOOK_SCRIPT" "$dir/.git/hooks/pre-push"
  chmod +x "$dir/.git/hooks/pre-push"

  mkdir -p "$dir.base"
  git -C "$dir.base" init -q --bare
  local gh_url="https://github.com/example/prepush-progress-fixture.git"
  git -C "$dir" config "url.$dir.base.insteadOf" "$gh_url"
  git -C "$dir" remote add origin "$gh_url"
  git -C "$dir" -c core.hooksPath=/dev/null push -q "$dir.base" HEAD:refs/heads/main
  git -C "$dir" fetch -q origin main
  git -C "$dir" checkout -q -b agy/test-topic
  printf 'change\n' >> "$dir/README.md"
  git -C "$dir" add README.md
  git -C "$dir" commit -q -m "topic change"
}

printf '\n=== Test 1: check_installed_hook_sync warns with fix command on mismatch, never auto-overwrites ===\n'
(
  repo="$TMP_BASE/repo_sync"
  setup_temp_push_repo "$repo"
  cd "$repo" || exit 1
  unset AESOP_ROOT

  # Matching hook -> clean (rc 0, no mismatch warning)
  out_match=$(check_installed_hook_sync 2>&1)
  rc_match=$?
  [ "$rc_match" -eq 0 ] || { printf 'FAIL: matching hook returned rc=%s\n' "$rc_match"; exit 1; }
  if printf '%s' "$out_match" | grep -qi 'differ\|mismatch'; then
    printf 'FAIL: matching hook should not print mismatch warning: %s\n' "$out_match"
    exit 1
  fi

  # Stale installed hook -> prints byte sizes + cp fix command, never overwrites .git/hooks/pre-push
  stale_content='#!/usr/bin/env bash
# stale hook copy
exit 0
'
  printf '%s' "$stale_content" > "$repo/.git/hooks/pre-push"
  before_bytes=$(wc -c < "$repo/.git/hooks/pre-push" | tr -d '[:space:]')
  src_bytes=$(wc -c < "$repo/hooks/pre-push-policy.sh" | tr -d '[:space:]')

  out_diff=$(check_installed_hook_sync 2>&1)
  rc_diff=$?
  after_bytes=$(wc -c < "$repo/.git/hooks/pre-push" | tr -d '[:space:]')

  [ "$after_bytes" = "$before_bytes" ] || { printf 'FAIL: hook was auto-overwritten (%s -> %s)\n' "$before_bytes" "$after_bytes"; exit 1; }
  printf '%s' "$out_diff" | grep -q "$before_bytes" || { printf 'FAIL: mismatch output missing installed byte count (%s): %s\n' "$before_bytes" "$out_diff"; exit 1; }
  printf '%s' "$out_diff" | grep -q "$src_bytes" || { printf 'FAIL: mismatch output missing source byte count (%s): %s\n' "$src_bytes" "$out_diff"; exit 1; }
  printf '%s' "$out_diff" | grep -q 'cp ' || { printf 'FAIL: mismatch output missing cp fix command: %s\n' "$out_diff"; exit 1; }
  printf 'PASS: check_installed_hook_sync reports byte mismatch + fix command without overwriting\n'
)
record_result $?

printf '\n=== Test 2: main() prints per-stage progress lines with stage name and elapsed s ===\n'
(
  repo="$TMP_BASE/repo_stages"
  setup_temp_push_repo "$repo"
  # Non-github origin so receipt stage completes fast without gh/signing network
  git -C "$repo" remote set-url origin "$repo.base"
  base_sha=$(git -C "$repo" rev-parse origin/main)
  tip_sha=$(git -C "$repo" rev-parse HEAD)
  tuple="refs/heads/agy/test-topic $tip_sha refs/heads/agy/test-topic $base_sha"

  err_out=$( cd "$repo" && unset AESOP_ROOT && printf '%s\n' "$tuple" | bash "$HOOK_SCRIPT" 2>&1 >/dev/null )
  rc=$?
  [ "$rc" -eq 0 ] || { printf 'FAIL: expected clean push rc=0, got %s:\n%s\n' "$rc" "$err_out"; exit 1; }

  for stage in branch_policy secret_scan claudemd_sync encoding_lint emit_receipt; do
    if ! printf '%s\n' "$err_out" | grep -Eq "\[pre-push\] stage +${stage} .*[0-9]+\.[0-9]+s"; then
      printf 'FAIL: missing progress line with elapsed s for stage %s:\n%s\n' "$stage" "$err_out"
      exit 1
    fi
  done
  printf 'PASS: per-stage progress lines printed with stage name and elapsed seconds\n'
)
record_result $?

printf '\n=== Test 3: failing stage prints BLOCKED reason immediately and halts before later stages ===\n'
(
  repo="$TMP_BASE/repo_fail_fast"
  setup_temp_push_repo "$repo"
  git -C "$repo" remote set-url origin "$repo.base"

  # Republish base tools where metrics_gate fails and encoding_lint writes a marker if reached
  git -C "$repo" checkout -q main
  printf '#!/usr/bin/env python3\nimport sys\nprint("UNVERIFIED_METRIC_42", file=sys.stderr)\nsys.exit(1)\n' > "$repo/tools/metrics_gate.py"
  marker="$TMP_BASE/later_stage_ran.marker"
  marker_py="$marker"
  command -v cygpath >/dev/null 2>&1 && marker_py=$(cygpath -m "$marker")
  printf '#!/usr/bin/env python3\nimport pathlib, sys\npathlib.Path("%s").write_text("ran")\nsys.exit(0)\n' "$marker_py" > "$repo/tools/encoding_lint.py"
  git -C "$repo" add tools/metrics_gate.py tools/encoding_lint.py
  git -C "$repo" commit -q -m "failing metrics gate"
  git -C "$repo" -c core.hooksPath=/dev/null push -q "$repo.base" HEAD:refs/heads/main
  git -C "$repo" fetch -q origin main
  git -C "$repo" checkout -q agy/test-topic
  git -C "$repo" rebase -q origin/main

  base_sha=$(git -C "$repo" rev-parse origin/main)
  tip_sha=$(git -C "$repo" rev-parse HEAD)
  tuple="refs/heads/agy/test-topic $tip_sha refs/heads/agy/test-topic $base_sha"

  err_out=$( cd "$repo" && unset AESOP_ROOT && printf '%s\n' "$tuple" | bash "$HOOK_SCRIPT" 2>&1 >/dev/null )
  rc=$?
  [ "$rc" -ne 0 ] || { printf 'FAIL: expected failing metrics gate to block push\n'; exit 1; }
  [ ! -e "$marker" ] || { printf 'FAIL: later stage encoding_lint ran after metrics failed\n'; exit 1; }
  if ! printf '%s\n' "$err_out" | grep -Eq '\[pre-push\] BLOCKED stage metrics \([0-9]+\.[0-9]+s\):'; then
    printf 'FAIL: missing immediate BLOCKED stage line for metrics:\n%s\n' "$err_out"
    exit 1
  fi
  printf 'PASS: BLOCKED reason printed immediately when stage fails; later stages do not run\n'
)
record_result $?

printf '\n=== Test 4: emit_receipt streams per-Python-shard progress and immediate BLOCKED before slow shard finishes ===\n'
(
  "$py_bin" - "$EMIT_SCRIPT" "$TMP_BASE" <<'PYEOF'
import importlib.util
import io
import sys
import time
from pathlib import Path

emit_path = Path(sys.argv[1])
tmp_base = Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("emit_receipt_mod", str(emit_path))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

events = []
t0 = time.monotonic()

class RecordingStderr(io.TextIOBase):
    def write(self, s):
        if s.strip():
            for line in s.splitlines():
                if line.strip():
                    events.append((time.monotonic() - t0, line.strip()))
        return len(s)
    def flush(self):
        pass

fast_fail = mod.PartSpec(lambda repo: [sys.executable, "-c", "import sys, time; time.sleep(0.1); sys.exit(1)"])
slow_ok = mod.PartSpec(lambda repo: [sys.executable, "-c", "import sys, time; time.sleep(1.2); print('Ran 5 tests'); sys.exit(0)"])
reg = {"py-shard-0": fast_fail, "py-shard-1": slow_ok}

non_git_dir = tmp_base / "shard_stream_dir"
non_git_dir.mkdir(parents=True, exist_ok=True)

real_err = sys.stderr
sys.stderr = RecordingStderr()
try:
    parts, skipped = mod.run_matrix(non_git_dir, ["py-shard-0", "py-shard-1"], registry=reg, jobs=2)
finally:
    sys.stderr = real_err

blocked_events = [(ts, ln) for ts, ln in events if "BLOCKED" in ln and "py-shard-0" in ln]
ok_events = [(ts, ln) for ts, ln in events if "py-shard-1" in ln and "s)" in ln]

if not blocked_events:
    print(f"FAIL: missing live BLOCKED line for py-shard-0; events={events}")
    sys.exit(1)
if not ok_events:
    print(f"FAIL: missing live progress line with elapsed s for py-shard-1; events={events}")
    sys.exit(1)
if blocked_events[0][0] >= ok_events[0][0]:
    print(f"FAIL: py-shard-0 BLOCKED arrived at {blocked_events[0][0]:.2f}s >= py-shard-1 finish {ok_events[0][0]:.2f}s (buffered instead of immediate!)")
    sys.exit(1)
print(f"PASS: py-shard-0 BLOCKED streamed at {blocked_events[0][0]:.2f}s before py-shard-1 finished at {ok_events[0][0]:.2f}s")
PYEOF
)
record_result $?

printf '\n=== Summary: %d passed, %d failed ===\n' "$passed" "$failed"
[ "$failed" -eq 0 ]
