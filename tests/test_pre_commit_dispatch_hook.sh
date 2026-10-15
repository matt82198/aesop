#!/usr/bin/env bash
# Behavioural tests for hooks/pre-commit-dispatch-lint.sh (F-05).
# Each case builds a temp git repo, copies in the hook + tools/dispatch_lint.py,
# stages a file, runs the hook, and asserts its exit code. Exit 0 = all pass.
set -uo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
HOOK_SRC="$REPO_ROOT/hooks/pre-commit-dispatch-lint.sh"
TOOL_SRC="$REPO_ROOT/tools/dispatch_lint.py"

PASS=0
FAIL=0
TMPROOT=$(mktemp -d)
trap 'rm -rf "$TMPROOT"' EXIT

# Build a fresh repo: $1 = dir. Copies the hook and the linter into it.
make_repo() {
  local dir=$1
  mkdir -p "$dir/hooks" "$dir/tools" "$dir/docs"
  git -C "$dir" init -q
  git -C "$dir" config user.email "test@example.com"
  git -C "$dir" config user.name "Test User"
  cp "$HOOK_SRC" "$dir/hooks/pre-commit-dispatch-lint.sh"
  cp "$TOOL_SRC" "$dir/tools/dispatch_lint.py"
  printf 'seed\n' > "$dir/README.md"
  git -C "$dir" add README.md
  git -C "$dir" commit -q -m "seed"
}

# Runs the hook inside $1 and records its exit code in RC, output in OUT.
run_hook() {
  local dir=$1
  OUT=$(cd "$dir" && bash hooks/pre-commit-dispatch-lint.sh 2>&1)
  RC=$?
}

check() {
  local name=$1 want=$2
  if [ "$RC" -eq "$want" ]; then
    printf 'PASS: %s (exit %s)\n' "$name" "$RC"
    PASS=$((PASS + 1))
  else
    printf 'FAIL: %s (want exit %s, got %s)\n' "$name" "$want" "$RC"
    printf '%s\n' "$OUT" | sed 's/^/    | /'
    FAIL=$((FAIL + 1))
  fi
}

# A dispatch brief with a forbidden manual merge. The agent-call marker is
# assembled at runtime so this test source itself is not a dispatch template.
write_violation() {
  local marker="Ag""ent("
  {
    printf '# Dispatch brief\n\n%s\n' "$marker"
    printf '  prompt="merge it: gh pr merge 5 --admin"\n)\n'
  } > "$1"
}

write_clean() {
  local marker="Ag""ent("
  {
    printf '# Dispatch brief\n\n%s\n' "$marker"
    printf '  prompt="arm it: gh pr merge 5 --auto --squash"\n)\n'
  } > "$1"
}

# Case 1 (red today): a staged violation must block with exit 1.
d="$TMPROOT/violation"; make_repo "$d"
write_violation "$d/docs/brief.md"; git -C "$d" add docs/brief.md
run_hook "$d"; check "staged dispatch violation is blocked" 1

# Case 2: a clean staged file passes with exit 0.
d="$TMPROOT/clean"; make_repo "$d"
write_clean "$d/docs/brief.md"; git -C "$d" add docs/brief.md
run_hook "$d"; check "clean staged file passes" 0

# Case 3: nothing staged passes with exit 0.
d="$TMPROOT/nothing"; make_repo "$d"
run_hook "$d"; check "empty commit passes" 0

# Case 4: the linter is missing from the tree -> fail closed (block).
d="$TMPROOT/notool"; make_repo "$d"
rm -f "$d/tools/dispatch_lint.py"
write_clean "$d/docs/brief.md"; git -C "$d" add docs/brief.md
run_hook "$d"; check "missing dispatch_lint.py blocks (fail closed)" 1

# Case 5: python cannot run -> fail closed (block) with a reason.
d="$TMPROOT/nopython"; make_repo "$d"
write_clean "$d/docs/brief.md"; git -C "$d" add docs/brief.md
OUT=$(cd "$d" && AESOP_PYTHON="$TMPROOT/no-such-python" bash hooks/pre-commit-dispatch-lint.sh 2>&1)
RC=$?
check "missing python blocks (fail closed)" 1
case "$OUT" in
  *python*) printf 'PASS: missing-python block prints a reason\n'; PASS=$((PASS + 1)) ;;
  *) printf 'FAIL: missing-python block printed no reason\n'; FAIL=$((FAIL + 1)) ;;
esac

printf '\nresult: %s passed, %s failed\n' "$PASS" "$FAIL"
if [ "$FAIL" -eq 0 ]; then
  exit 0
fi
exit 1
