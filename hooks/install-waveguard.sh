#!/usr/bin/env bash
# Installs the waveguard pre-commit hook into this repo's .git/hooks, backing up any existing pre-commit.
set -uo pipefail

main() {
  local repo_root
  repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
  if [ -z "$repo_root" ]; then
    printf 'Error: Not in a git repository\n' >&2
    return 1
  fi

  local hooks_dir="$repo_root/.git/hooks"
  local waveguard_src="$repo_root/hooks/pre-commit-waveguard.sh"
  local pre_commit_dest="$hooks_dir/pre-commit"
  local pre_commit_orig="$hooks_dir/pre-commit.waveguard-backup"

  if [ ! -f "$waveguard_src" ]; then
    printf 'Error: Source hook not found at %s\n' "$waveguard_src" >&2
    return 1
  fi

  mkdir -p "$hooks_dir"

  if [ -f "$pre_commit_dest" ]; then
    if grep -q 'pre-commit-waveguard' "$pre_commit_dest" 2>/dev/null; then
      printf 'Info: pre-commit hook already has waveguard; skipping.\n'
      return 0
    fi

    cp "$pre_commit_dest" "$pre_commit_orig"
    printf 'Info: Existing pre-commit hook backed up to %s\n' "$pre_commit_orig"
  fi

  cat > "$pre_commit_dest" <<'HOOK_WRAPPER'
#!/usr/bin/env bash
set -uo pipefail

repo_root=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -z "$repo_root" ]; then
  exit 1
fi

waveguard_hook="$repo_root/hooks/pre-commit-waveguard.sh"
if [ -f "$waveguard_hook" ]; then
  bash "$waveguard_hook"
  waveguard_exit=$?
  if [ $waveguard_exit -ne 0 ]; then
    exit $waveguard_exit
  fi
fi

dispatch_hook="$repo_root/hooks/pre-commit-dispatch-lint.sh"
if [ -f "$dispatch_hook" ]; then
  bash "$dispatch_hook"
  dispatch_exit=$?
  if [ $dispatch_exit -ne 0 ]; then
    exit $dispatch_exit
  fi
fi

backup_hook="$repo_root/.git/hooks/pre-commit.waveguard-backup"
if [ -f "$backup_hook" ] && [ -x "$backup_hook" ]; then
  bash "$backup_hook"
  exit $?
fi

exit 0
HOOK_WRAPPER

  chmod +x "$pre_commit_dest"
  printf 'Info: Installed waveguard pre-commit hook to %s\n' "$pre_commit_dest"
  return 0
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
