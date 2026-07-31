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
4. `check_import_resolution()` — runs `tools/import_resolution_check.py` on staged .py files (guardrail G5); parses via AST, resolves imports against repo structure + stdlib; exit 1 on unresolvable imports. Fail-open only when tool is absent (`import_check_skipped_tool_missing` logged); actual resolution failures are fail-closed.
5. `check_tracker_guard()` — runs `tools/tracker_guard.py --check` against live runtime state (`AESOP_STATE_ROOT`, default `$AESOP_ROOT/state`); exit 1 (push blocked, `tracker_guard_failure` logged) on zombie-resurrection detection. Wired here rather than CI because tracker.json is git-ignored runtime state a CI checkout never has. Fail-open only when the tool itself is absent (`tracker_guard_skipped_tool_missing` logged) — the hook installs into repos without an aesop checkout.
6. `check_claudemd_sync()` — runs `tools/claudemd_sync_gate.py --check` to verify domain code changes are accompanied by corresponding domain/CLAUDE.md updates; exit 1 on drift. Detects when domain directories change without documenting what changed. Fail-open only for missing tool.
7. `check_metrics()` — runs `tools/metrics_gate.py` to verify hard numeric claims (percentages, multipliers, dollar amounts) in markdown have source verification markers; exit 1 on unverified claims. Fail-open only for missing tool.
8. `check_encoding_lint()` — runs `tools/encoding_lint.py` with `--baseline .encoding-baseline.json`; flags `subprocess.run/check_output/Popen` with `text=True`/`universal_newlines=True` and no `encoding=` (the Windows cp1252 trap). Ratchets against a committed baseline so the existing backlog does not block pushes while new violations are fail-closed. Fail-open only when the tool or baseline is absent.
9. `check_test_coverage()` — runs `tools/verify_test_coverage.py --check`; detects test files no CI job runs (the fake-green class). Fail-closed on orphans; fail-open only when the tool is absent.
10. Policy violations trigger `log_block()` to append audit record (JSON-lines) before exit

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

**Test Command**: `bash hooks/pre-push-policy.sh --test` — runs 18 validation tests covering: branch policy (blocks main/master, allows feature/*, tag-only, mixed), secret scan (multi-ref, no-starvation), audit log (JSON format, escaping, hash-chain), hash verification, new documentation gates fail-open. Exit 0 = pass; exit 1 = fail.

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
- Fail-open for missing optional tooling (secret_scan.py absent → allow); fail-closed for policy checks (branch, marker, model)
- `AESOP_ROOT` env var or `$HOME/aesop` fallback; no hardcoded machine paths/usernames
- Local convenience defense only; real enforcement requires server-side branch protection (GitHub) and centralized audit logs

## hook_preflight.py — Interpreter health check

Verifies interpreters in hooks/daemons are present and executable. Detects missing/broken interpreters that silently fail.
Usage: `python tools/hook_preflight.py` — exit 0=all OK, 1=broken interpreter, 2=no checks performed.
Early guard in pre-push-policy.sh blocks push if Python missing (required for secret_scan.py).

## Dropped (reason)
- `docs/HOOK-INSTALL.md` comprehensive guide inlined above (GitHub config, troubleshooting, customization, rotation); refer to that file if org needs full runbook for distribution teams.
- Map of all domains: /CLAUDE.md
