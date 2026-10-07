# hooks/ — Git & Claude Code policy enforcement

**Purpose**: Installable git hooks (pre-push, pre-commit) and Claude Code hooks (PreToolUse) that gate commits/pushes with security & cost policies.

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only; dummy secrets are runtime-concatenated, never literal.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## pre-push-policy.sh

Runs on `git push` via `.git/hooks/pre-push` (symlink on Unix/macOS/Git Bash; copy on Windows).

**Checks & Exit Contract**:
1. `main()` TTY guard — rejects interactive hook invocation (tty stdin) with exit 1 before any checks (fail-closed); logs `interactive_invocation_blocked`. Real `git push` always pipes stdin; tty means human ran hook directly.
2. `check_branch_policy()` — blocks direct pushes to main/master; exit 1 on violation
3. `check_secret_scan()` — runs `tools/secret_scan.py --range` for each ref tuple on git pre-push stdin; exit 1 on findings. Scans all branches in multi-ref pushes (e.g., `git push --all`).
   **Commit range fix**: For NEW branches (remote ref all-zeros), `get_commit_range()` now computes the base using `git merge-base <local-sha> origin/main` (falling back to origin/master, then local main/master), not the local main ref. This ensures correctness in worktrees where local main is checked out from a stale commit far behind origin/main; the remote reference is authoritative.
4. `check_import_resolution()` — runs `tools/import_resolution_check.py --range` once per ref tuple on git pre-push stdin (guardrail G5), reusing `get_commit_range()` exactly as `check_secret_scan()` does; AST-parses each pushed .py file (blob read at the range TIP, not the working tree) and resolves its imports against repo structure + stdlib + environment; exit 1 on unresolvable imports. Delete-only (`import_check_skipped_delete_only_push`) and empty stdin (`import_check_skipped_empty_stdin`) pass; malformed stdin is fail-closed (`import_check_stdin_parse_failed`). Fail-open only when the tool is absent (`import_check_skipped_tool_missing`).
   **Was vacuously green.** It previously ran with no arguments, so it evaluated `git diff --cached`. A pre-push hook runs *after* the commit, so the index is EMPTY: the gate printed "No staged Python files found" and exited 0 on every normal push — it had never actually run. Any change here must keep feeding it the pushed range (`check_import_resolution <<< "$prepush_stdin"`); a fail-closed gate that evaluates nothing is worse than no gate, because it reads as protection.
5. `check_conflict_markers()` — runs `tools/conflict_marker_check.py --staged` once per ref tuple on git pre-push stdin (reusing `get_commit_range()` exactly as `check_import_resolution()` does); scans only the ADDED lines in that range's diff for literal unresolved conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`, `|||||||`); exit 1 on any marker found (`conflict_marker_check_failure` logged). Added after the PR #834 incident, where a merge commit (b2c4db77) landed a literal `<<<<<<< HEAD` block in `tools/CLAUDE.md` on main and every other gate (claudemd_lint, claudemd_sync_gate, all PR CI) passed. Delete-only/empty-stdin pass; malformed stdin is fail-closed. Fail-open only when the tool is absent (`conflict_marker_check_skipped_no_aesop_tools`).
6. `check_tracker_guard()` — runs `tools/tracker_guard.py --check` against live runtime state (`AESOP_STATE_ROOT`, default `$AESOP_ROOT/state`); exit 1 (push blocked, `tracker_guard_failure` logged) on zombie-resurrection detection. Wired here rather than CI because tracker.json is git-ignored runtime state a CI checkout never has. Fail-open only when the tool itself is absent (`tracker_guard_skipped_tool_missing` logged) — the hook installs into repos without an aesop checkout.
7. `check_claudemd_sync()` — runs `tools/claudemd_sync_gate.py --check` to verify domain code changes are accompanied by corresponding domain/CLAUDE.md updates; exit 1 on drift. Detects when domain directories change without documenting what changed. Fail-open only for missing tool.
8. `check_gen_tool_index()` — runs `tools/gen_tool_index.py --check` to verify tools/INDEX.md is in sync with per-tool INDEX: docstring lines; exit 1 on drift or any tool missing its INDEX: line (fail-closed). Wired after CLAUDE.md sync gate. Fail-open only for missing tool.
9. `check_metrics()` — runs `tools/metrics_gate.py` to verify hard numeric claims (percentages, multipliers, dollar amounts) in markdown have source verification markers; exit 1 on unverified claims. Fail-open only for missing tool.
10. Validates that the documented test suite count stays in sync with the actual number of test suites in the repo. Implemented as `check_test_suite_count()`, which runs `tools/verify_test_suite_count.py --check`; exit 1 on mismatch. Detects when test suite counts drift without updating documentation. Fail-open only when the tool is absent.
11. `check_claudemd_headroom()` — runs `tools/claudemd_lint.py --headroom --base-ref ${AESOP_HEADROOM_BASE_REF:-origin/main}`; previews the merge and lints the UNION's CLAUDE.md line cap, catching the cascade where a branch passes at 149/150 but merges to 151. Exit 1 (a union busts its cap) is fail-closed; exit 2 (union unreadable — base ref never fetched, shallow clone, un-previewable merge) and a missing tool both fail open, logging `claudemd_headroom_skipped_unreadable`/`_tool_missing`.
12. `check_encoding_lint()` — runs `tools/encoding_lint.py --check` over the WHOLE repo. There is NO baseline: the tool has no `--baseline` flag, so this gate is fail-closed on any finding anywhere and one violation blocks every Python-touching push. (The committed `.encoding-baseline.json` that nothing read was removed as a dead baseline with no consumer -- see the dead-baseline liveness guardrail.) Flags `subprocess.run/check_output/Popen` with `text=True`/`universal_newlines=True` and no `encoding=` (the Windows cp1252 trap), AND `subprocess.*` that sets `encoding=` without a safe `errors=` handler — strict decoding kills subprocess's reader thread, leaves stdout None, and crashed the merge queue on 24+ consecutive passes while this gate reported clean. Fail-open only when the tool or a python interpreter is absent. **Worktree fix**: calls `resolve_aesop_root()` to resolve the pushed repo (not a hardcoded primary tree path), so gate fixes on a branch take effect immediately from that branch's push.
13. `check_test_coverage()` — runs `tools/verify_test_coverage.py --check`; detects test files no CI job runs (the fake-green class). Fail-closed on orphans; fail-open only when the tool is absent. **Worktree fix**: calls `resolve_aesop_root()` to resolve the pushed repo (not a hardcoded primary tree path), so gate fixes on a branch take effect immediately from that branch's push.
14. `check_generated_paths()` — runs `tools/generated_paths.py --check` over the pushed diff (each ref tuple's `<remote-sha>..<local-sha>` range through `git diff --name-only`; paths go over stdin, not argv, so a large diff cannot blow the command-line length limit); exit 1 (`generated_path_hand_edit` logged) when the push touches a registered machine-generated path, and the message names the owning generator. A generated file has exactly one legitimate writer, so a hand edit is both silently reverted on the next regeneration and a guaranteed conflict with every concurrent lane — this gate is what kills that contended-file class. Escape hatch `AESOP_ALLOW_GENERATED=1` (honored inside the tool; exactly `1`, nothing else) is the DESIGNED writer path for generator / merge-train regeneration / daemon pushes, not a weakening — an ordinary push never sets it. Fail-open only when the tool or python is absent; delete-only or empty stdin simply means no diff to classify (malformed stdin is already fail-closed by `check_secret_scan`, which runs first).
15. `check_generated_regen()` — runs `tools/generated_push_gate.py --range <remote-sha>..<local-sha>` (one `--range` per ref tuple, same captured stdin as `check_generated_paths()`), preceded by `ensure_merge_drivers()` (fail-open, quiet `tools/install_merge_drivers.py`: registers the `aesop-json-union` and `aesop-regen` merge drivers in this clone's config so no pushing clone can forget). The gate verifies the pushed COMMIT, not the working tree: when a range touches a registered regenerable artifact (`tools/generated_paths.py::REGISTRY` entries with a `regen` argv — today `tools/INDEX.md`), or the merge driver's per-worktree `.needs-regen` stamp names one, it checks the tip out in a throwaway detached worktree, runs the registered generator there, and exits 1 (`generated_artifact_stale` logged) printing exactly `run: python tools/gen_tool_index.py --regenerate && git add tools/INDEX.md` when the committed bytes differ. Closes the gap behind #784/#856/#739: `check_gen_tool_index()` (item 8) runs `--check` on the WORKING TREE, so a regenerated-but-uncommitted tree or a `union`-stale merge commit pushed green and failed ci(0). No escape hatch (a stale artifact is never a legitimate write); fail-closed when `tools/` exists but the script or python is missing; skip only with no aesop checkout; delete-only/empty stdin = nothing to verify.
16. `check_linux_shape()` — runs `tools/linux_shape_check.py --range <commit-range>` for commits touching shell/workflow/Node-test files to catch platform-specific failures via WSL-based test execution. Detects changes to `*.sh`, `hooks/*`, `.github/workflows/*.yml`, `tests/**/*.test.mjs` and runs owning test suites under WSL (e.g., `wsl bash -lc 'cd /mnt/c/... && bash tools/run_shell_tests.sh'`), with Node tests run under WSL with `USERPROFILE` unset to catch environment variable assumptions (fixed #864: isolated-home assumption in tripwire test, #870: shell test failures on Ubuntu CI). Exit contract: skip if no changes detected, or (Windows without WSL) print NOTICE + exit 0 (unless `AESOP_REQUIRE_LINUX_SHAPE=1`); fail-closed on test failure. Fail-open only when tools/ absent (no aesop checkout).
17. `check_pyflakes_ratchet()` (guardrail G15) — runs `tools/pyflakes_gate.py --root <aesop_root> --baseline .pyflakes-baseline.json`; scans tools/, bin/, ui/, state_store/, driver/, monitor/, daemons/, tests/, bench/ with `python -m pyflakes`. Never lets unused-import / unused-variable debt grow: NEW findings above baseline block the push (exit 1, `pyflakes_ratchet_failure` logged); STALE entries (burned-down findings) also block until the baseline is regenerated with `--update-baseline` (review-only; CI never passes it). Baseline is an exact-match bidirectional ratchet: pass only when the current scan EXACTLY matches. Exit codes: 0 = clean/baselined, 1 = new-or-stale findings (fail-CLOSED), 2 = error condition (pyflakes not installed, baseline unreadable, etc; fail-OPEN with WARN audit event `pyflakes_ratchet_skipped_tool_error`). Pyflakes is a dev-only dependency (not installed by default; CI installs it in the "Install Python test dependencies" step). Opt-out via `AESOP_PYFLAKES_SKIP=1` (exact string match) for emergency escapes (logs `pyflakes_ratchet_skipped_env_opt_out`). Fail-open only when the tool is absent or pyflakes is not importable; actual baseline mismatches are fail-closed.
18. `check_emit_receipt()` — runs LAST, only after every check above has passed: first calls `env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_PREFIX -u GIT_COMMON_DIR -u GIT_OBJECT_DIRECTORY tools/receipt_flush.py --repo <aesop_root>` (fail-open, bounded, ~10s) to flush any prior spooled receipts whose shas now exist on GitHub (flush also runs with scrubbed env for isolation), then runs the same `env -u` prefix with `tools/emit_receipt.py --repo <aesop_root> --post` (docs/RECEIPT-GATE.md). Day-1 measurement found 41 merged PRs and ZERO receipts (nothing ever ran the emitter); this makes emission automatic instead of lane-memory-dependent. **Isolation and spool-and-flush**: the `env -u` prefix scrubs git hook environment variables to prevent matrix parts from committing to the lane's repo via inherited GIT_DIR/GIT_WORK_TREE (defense in depth; `emit_receipt.py` also scrubs these vars); when `emit_receipt.py --post` gets 422 (commit not found on GitHub yet because we're in pre-push), instead of raising, it writes the signed envelope to `state/receipts/spool/<head_sha>.json` and returns "spooled"; the flush step retries any prior spooled receipts before emitting a new one, ensuring receipts are published once shas are pushed. ALWAYS fail-open by construction — a missing tool/python/`gh`/signing-key material, or a red matrix part, prints a one-line WARN and never blocks the push (it is an honesty signal, not a gate, matching the hosted `verify-receipt` check's own non-required status). The default matrix re-runs all 4 python shards (measured ~500s for the slowest shard alone), so the emit call is wrapped in `timeout` (`AESOP_RECEIPT_TIMEOUT`, default 900s) — a timeout WARNs and fails open exactly like every other branch, never hangs the push. `AESOP_RECEIPT_EMIT=0` opts out entirely. **Key path resolution**: hooks/pre-push-policy.sh checks for signing key material using the same precedence as `tools/receipt_common.py::resolve_receipt_key_path()` — `AESOP_RECEIPT_KEY` env var, then `AESOP_RECEIPT_HMAC_SECRET` env var, then `~/.aesop/receipt_key.pem` (or `$AESOP_HOME/.aesop/receipt_key.pem` if AESOP_HOME is set). Only if none of the three exist does the hook warn and fail-open; lane shells without the env vars can still emit receipts via the default path. Tests: `tests/test_pre_push_policy.sh`.
19. Policy violations trigger `log_block()` to append audit record (JSON-lines) before exit

**Audit Ledger**: Append-only path: `${AESOP_ROOT:-$HOME/aesop}/state/SECURITY-AUDIT.log` (git-ignored). 
Schema: `{"seq":N,"prev_hash":"SHA256_OF_PREV_LINE","ts":"2025-07-12T14:32:01Z","repo":"aesop","event":"push_blocked","reason":"secret_scan_failure"|"push_to_protected_branch","user":"alice"}`
- `seq`: Monotonically increasing (starts 1); detects truncation.
- `prev_hash`: SHA-256 of prior line (no newline); first entry = `"GENESIS"`. Detects tampering.
- All string values must be JSON-escaped (backslash → `\\`, quote → `\"`, control chars → `\uXXXX`).
- Concurrent writes protected by atomic directory lock (`.audit-log-lock/`, 300s stale recovery); tail-hash sidecar (`state/.audit-tail-hash`) anchors against truncation.

**Installation**:
- Symlink (Unix/macOS/Git Bash): `ln -s ../../hooks/pre-push-policy.sh .git/hooks/pre-push && chmod +x .git/hooks/pre-push`
- Copy (Windows): `cp hooks/pre-push-policy.sh .git/hooks/pre-push` (or PowerShell `Copy-Item`)
- Auto-installed by scaffold; `npx @matt82198/aesop [dir] --force` to replace existing hook.

**Test Command**: `bash hooks/pre-push-policy.sh --test` — runs 27 validation tests covering: branch policy (blocks main/master, allows feature/*, tag-only, mixed), secret scan (multi-ref, no-starvation), audit log (JSON format, escaping, hash-chain), hash verification, documentation gates skipping when there is no aesop checkout, the same gates failing CLOSED when `tools/` exists but their script is missing, tool index gate validation (passes when INDEX in sync, fails when INDEX line missing), CLAUDE.md headroom exit contract (0/1/2), the generated-path gate (fail-open when tool missing, blocks a registered path, passes an ordinary one), the conflict-marker gate (fail-open when tool missing, blocks a push introducing a literal marker), and `get_commit_range()` behavior (uses origin/main for new branches in worktrees with stale local main, not the stale ref). Exit 0 = pass; exit 1 = fail. `python -m unittest tests.test_generated_paths` drives `check_generated_paths()` against the REAL registry over fixture git repos (rejection, generator naming, escape hatch, delete-only, fail-open). `python tests/test_conflict_marker_check.py` drives `tools/conflict_marker_check.py` directly (full-tree + `--staged` diff modes, allowlist, inline suppression, binary skip, fail-closed on a bad range).

### Gate tool resolution (fail-closed)

`gate_tool_status()` classifies a missing gate script into `ok` / `skip` / `missing`:

- **skip** — `$aesop_root/tools` does not exist at all: no aesop checkout, nothing to gate. Logs `<gate>_skipped_no_aesop_tools`, returns 0. This is the adopter case the fail-open was for.
- **missing** — `tools/` exists but this gate's script does not. **Blocks the push** (`<gate>_tool_missing`). Previously every absence was treated as `skip`, so deleting, renaming, or failing to ship one gate script silently disabled it in the repo that owns it — a green push that verified nothing.
- Interpreter absence is likewise fail-closed (`<gate>_no_python`); the top-of-file guard already hard-requires Python, so a fail-open branch there was dead code that only looked like a safety valve.
- **Executability is not required.** Gates run as `"$py_bin" "$script"`, so the exec bit is irrelevant; demanding `-x` turned any checkout without exec bits into a silently ungated one.
- `check_encoding_lint` and `check_test_coverage` now resolve their root via `resolve_aesop_root()`. They still used the hardcoded `${AESOP_ROOT:-$HOME/aesop}` fallback, which ran the primary tree's script when pushing from a worktree and skipped the gate outright on any machine without `~/aesop`.

**Verify Audit Log**: `bash hooks/pre-push-policy.sh --verify-audit-log` — detects hash-chain breaks and tail truncation via sidecar anchor.

## pre-commit-waveguard.sh

Prevents accidental commits to PRIMARY tree during orchestrated wave cycles. Runs on `git commit` via `.git/hooks/pre-commit`.

**Mechanism**: Orchestrator sets marker file `state/.wave-in-flight` in PRIMARY tree only (git-ignored, so sibling worktrees do NOT inherit it during checkout). Hook resolves marker relative to CURRENT tree via `git rev-parse --show-toplevel` — **NOT hardcoded `$AESOP_ROOT`** (that resolved to primary from every worktree and blocked entire fleet mid-wave: wave-24 incident). Primary tree (marker present) → exit 1; sibling worktree (no marker) → exit 0.

**Error Message**: `Error: Wave in flight in this tree (<marker_path>). Commit from a sibling worktree, or clear the marker to override.`

**Installation**: `bash hooks/install-waveguard.sh` idempotently installs into `.git/hooks/pre-commit`. If a pre-commit hook already exists, backs it up (`.git/hooks/pre-commit.waveguard-backup`) and chains both (waveguard first, existing hook second).

**Exit Contract**: Exit 0 = marker absent, commit allowed (normal); Exit 1 = marker present, commit blocked.

## force-model-policy.mjs

Claude Code **PreToolUse** hook enforcing "subagents are always Haiku" cardinal rule (cost optimization).

**Policy**:
- Main orchestrator (Fable/Opus on primary): no constraint
- Subagent dispatch (Agent/Task): enforce Haiku or `cardinal_rules.subagent_model` from `aesop.config.json` (searched in `$AESOP_ROOT`, then cwd). Non-compliant model rewritten before dispatch.
- **Escape hatch**: Prompt containing `[[ALLOW-NON-HAIKU]]` bypasses rewrite; escape logged to `state/MODEL-POLICY-ESCAPES.log` (JSON-lines: ts, event, tool, session_id, cwd, description, requested_model, prompt_head).

**Fail-open reliability**: Malformed stdin → no output, exit 0. Hook never crashes harness or logs payload contents. Stdin read raced against 2s timeout.

**Registration (`.claude/settings.json`)**:
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Agent|Task",
        "hooks": [{"type":"command","command":"node \"$CLAUDE_PROJECT_DIR/hooks/claude/force-model-policy.mjs\""}]
      }
    ]
  }
}
```

**Test Command**: `node --test tests/force-model-policy.test.mjs` (the .mjs itself has no --test mode). Validates Haiku allowed on subagents, non-Haiku (e.g., Opus) blocked, orchestrator not subject to policy, JSON logging format valid. Exit 0 = pass; exit 1 = fail.

## pre-commit-dispatch-lint.sh

Pre-commit hook running `tools/dispatch_lint.py` on staged files. Blocks commits containing dispatch policy violations (forbidden flags like `--admin`, `--no-verify`, `git stash`, credential hunting). BASH_SOURCE guarded. Fail-open when no violations detected.

## Key Invariants
- Bash required (explicit shebang), CRLF-safe
- Tolerate git pre-push stdin (ref list: `<local-ref> <local-oid> <remote-ref> <remote-oid>` per line) + optional args without crashing
- Fail-closed for policy checks (branch, marker, model) AND for any gate whose script is missing from an existing `tools/`; skip only when there is no aesop checkout at all (see § Gate tool resolution). `secret_scan.py` absent is already fail-closed (FATAL, push denied)
- `AESOP_ROOT` env var or `$HOME/aesop` fallback; no hardcoded machine paths/usernames
- Local convenience defense only; real enforcement requires server-side branch protection (GitHub) and centralized audit logs

## hook_preflight.py — Interpreter health check

Verifies interpreters in hooks/daemons are present and executable. Detects missing/broken interpreters that silently fail.
Usage: `python tools/hook_preflight.py` — exit 0=all OK, 1=broken interpreter, 2=no checks performed.
Early guard in pre-push-policy.sh blocks push if Python missing (required for secret_scan.py).

## Dropped (reason)
- `docs/HOOK-INSTALL.md` comprehensive guide inlined above (GitHub config, troubleshooting, customization, rotation); refer to that file if org needs full runbook for distribution teams.
- Map of all domains: /CLAUDE.md
