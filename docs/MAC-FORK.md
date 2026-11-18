# Operating the Private macOS Fork

Runbook for keeping the private macOS port branch synchronized with upstream `origin/main` using `tools/mac_fork_sync.py`.

## Architecture

- **Upstream remote (`origin`)**: main fleet branch (`main`), treated as read-only by this controller.
- **Fork remote (`mac-fork`)**: private macOS remote holding the Mac port branch (for example `port/macos-sweep`).
- **Mac-owned paths**: `.github/workflows/`, `hooks/`, and `daemons/` contain Mac-specific runner and shell adaptations.

The controller is fail-closed: it refuses dirty trees, detached HEAD states, and any `--fork-remote origin` target pointing at `main` or `master` (exit `2`).

## Daily Workflow

### 1. Check sync status

```bash
python tools/mac_fork_sync.py --repo . --status --json
```

Reports `branch`, `dirty`, `ahead`, `behind`, and `upstream_commits` waiting on `origin/main`.

### 2. Preview merge without touching the working tree

```bash
python tools/mac_fork_sync.py --repo . --plan --json
```

Runs `git merge-tree --write-tree` in the object database only. Exits `0` when the merge is clean, or `1` with `conflicts` and `mac_owned_conflicts` listed if upstream touched Mac-owned paths.

### 3. Sync, audit invariants, and push

```bash
python tools/mac_fork_sync.py --repo . --sync --push \
  --fork-remote mac-fork --fork-branch port/macos-sweep --json
```

Sync order:
1. Verify clean working tree, attached branch, and non-protected fork target (exit `2` on violation).
2. Fetch `origin` and merge `origin/main` into the local Mac branch.
3. On merge conflict, abort and reset to the pre-merge SHA (exit `1`).
4. Run `audit_mac_invariants(repo)`:
   - `.github/workflows/ci.yml` (`jobs.ci`) must not reintroduce `runs-on: ubuntu-latest`.
   - Tracked files must not introduce un-baselined Windows user paths outside `.portability-baseline.json`.
5. If the audit fails, roll back via `git reset --hard <pre_merge_sha>` and exit `1`.
6. When `--push` is set and the audit passes, push `HEAD` to `mac-fork` (`port/macos-sweep`).

## Handling Conflicts or Audit Failures

- **Exit `1` on `--plan` or `MERGE_CONFLICT`**: Inspect `mac_owned_conflicts`, merge `origin/main` manually in a clean worktree preserving Mac-port behavior on both sides, run local tests, and re-run `--status`.
- **Exit `1` on `AUDIT_FAILED`**: The working tree has already been reset to `pre_merge_sha`. Fix the offending upstream change in a dedicated integration commit (or update `.portability-baseline.json` only after review) before pushing.
- **Exit `2`**: Clean or commit local changes (`git status --porcelain` must be empty), attach HEAD to your Mac branch, or correct `--fork-remote` / `--fork-branch`.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Status retrieved, merge plan clean, or sync + audit (+ push) succeeded |
| `1` | Plan conflict, merge conflict, or post-merge Mac invariant audit failed (rolled back) |
| `2` | Precondition refusal (dirty tree, detached HEAD, protected `origin/main` or `master` target) or git error |
