# Git Pre-Push Hook Setup

**TL;DR**: The hook is **auto-installed during scaffold**. Real enforcement requires server-side GitHub branch protection.

## What the Hook Does

`hooks/pre-push-policy.sh` enforces:
1. **Branch Policy** — No direct pushes to `main`/`master` (feature branches only)
2. **Secret Scan** — `tools/secret_scan.py --staged` blocks credentials

## Security Model

**IMPORTANT**: Local hook is a **convenience defense only**—NOT cryptographic protection. Bypass with `git push --no-verify`.

**Real enforcement**: Pair with GitHub branch protection (server-side). See below.

## Auto-Installation

Scaffold auto-installs the hook (symlink on Unix, copy on Windows):

```bash
npx @matt82198/aesop my-fleet
```

Manual install:
```bash
# Option 1: Symlink (Unix/macOS/Git Bash)
ln -s ../../hooks/pre-push-policy.sh .git/hooks/pre-push

# Option 2: Copy (Windows)
cp hooks/pre-push-policy.sh .git/hooks/pre-push
```

Test:
```bash
bash hooks/pre-push-policy.sh --test
```

## GitHub Configuration (Server-Side Enforcement)

To pair this local hook with real enforcement, enable branch protection on GitHub:

### Step 1: Create a Protected Branch

1. Go to your GitHub repo **Settings** > **Branches**.
2. Click **Add rule** under "Branch protection rules".
3. Enter branch name pattern: `main` (or `master`, depending on your default branch).

### Step 2: Enable Required Protections

- **Require pull request reviews before merging**: ✓ (enforces PR review workflow)
- **Require status checks to pass before merging**: ✓ (use this for CI)
- **Require branches to be up to date before merging**: ✓
- **Restrict who can push to matching branches**: ✓ (optional; allows only admins to push to main)

### Step 3: Dismiss Stale Reviews (Recommended)

- **Dismiss stale pull request approvals when new commits are pushed**: ✓

### Example: Minimal Protected Branch Rule for `main`

| Setting | Value |
|---------|-------|
| Branch name pattern | `main` |
| Require pull request reviews | Yes (1 approval) |
| Require status checks to pass | Yes (if you have CI) |
| Require branches up to date | Yes |
| Restrict pushes to | Admins only |

Once this is configured, even if a developer bypasses the local hook with `--no-verify`, the remote will **refuse the push**.

## Audit Log Format & Integrity

Each block writes one JSON line to `${AESOP_ROOT:-$HOME/aesop}/state/SECURITY-AUDIT.log`:

```json
{"seq":1,"prev_hash":"GENESIS","ts":"2025-07-12T14:32:01Z","repo":"aesop","event":"push_blocked","reason":"secret_scan_failure","user":"alice"}
{"seq":2,"prev_hash":"f4b92becb47baa447e839330cf3c0c6e8dea947acc9ec372bb99063ee416d036","ts":"2025-07-12T14:32:02Z","repo":"aesop","event":"push_blocked","reason":"secret_scan_failure","user":"bob"}
```

**Fields:**
- `seq`: Monotonically increasing sequence number (starts at 1). Enables detection of truncation or missing entries.
- `prev_hash`: SHA-256 hash of the previous line (without trailing newline). First entry uses `"GENESIS"`. Enables tampering detection.
- `ts`: ISO-8601 UTC timestamp
- `repo`: Repository basename
- `event`: Always `push_blocked` (or `secret_scan_unavailable`)
- `reason`: `push_to_protected_branch`, `secret_scan_failure`, or other block reason
- `user`: Git user.name (fallback: "unknown"); all special characters and control chars are JSON-escaped

**Write Safety & Concurrent Access:**
All audit log writes are protected by a file-system atomic lock (`.audit-log-lock/` directory). This ensures that concurrent pushes from different repositories (or simultaneous local pushes) do not corrupt the hash chain. The lock has a 300-second stale-lock recovery mechanism to handle crashed holders.

**Tail Hash Anchor:**
Each write also updates `${AESOP_ROOT:-$HOME/aesop}/state/.audit-tail-hash` with the SHA-256 hash of the newly appended line. This sidecar file acts as an anchor against tail truncation.

### Verifying Audit Log Integrity

The hash chain and truncation anchor allow you to detect if someone edited, deleted, or truncated audit log entries:

```bash
bash hooks/pre-push-policy.sh --verify-audit-log
```

Output on intact log:
```
Audit log verification OK (42 entries)
```

Output if line tampering is detected:
```
Error: Hash chain broken at line 15
  Expected prev_hash: f4b92becb47baa447e839330cf3c0c6e8dea947acc9ec372bb99063ee416d036
  Actual prev_hash: abc123...
```

Output if tail truncation is detected:
```
TRUNCATION SUSPECTED: Tail hash mismatch (stored: f4b..., actual: 8e6...)
```

The truncation detection compares the stored tail hash (`state/.audit-tail-hash`) against the actual SHA-256 hash of the current log's last line. If someone deletes the last N lines, the hashes will not match.

**Note**: Verification is a convenience check; it does not prevent tampering on a machine where the attacker has file system access. For real auditability, centralize audit logs to a secure remote (e.g., CloudWatch, Datadog, or a separate immutable log server).

Parse the log with standard JSON tools:

```bash
cat state/SECURITY-AUDIT.log | jq '.reason' | sort | uniq -c
```

## Org Distribution & Customization

### Standard Distribution

1. Commit the hook to a shared repo or policy repository.
2. Instruct teams to symlink or copy per option above.
3. Communicate audit log location and parsing examples.

### Customizing Checks

To add org-specific rules (e.g., branch naming conventions, required CI status):

1. Edit `hooks/pre-push-policy.sh` and add a new check function:
   ```bash
   check_my_org_policy() {
     # your logic here
     return 0 or 1
   }
   ```

2. Call it from `main()` before exit, and log blocks:
   ```bash
   if ! check_my_org_policy; then
     log_block "my_org_policy_violation"
     exit 1
   fi
   ```

3. Update `run_test_mode()` to include test cases for the new check.

4. Commit and redistribute.

### Disabling in CI/CD

If CI systems need to bypass the hook (e.g., for automated releases), set:

```bash
export GIT_SKIP_HOOKS=pre-push
git push
```

Or use `git push --no-verify` (explicitly allowing humans to opt out).

## Audit Log Rotation

The audit log is append-only. For long-running deployments, consider log rotation:

```bash
if [ -f state/SECURITY-AUDIT.log ] && [ $(stat -f%z state/SECURITY-AUDIT.log 2>/dev/null || stat -c%s state/SECURITY-AUDIT.log) -gt 1000000 ]; then
  mv state/SECURITY-AUDIT.log "state/SECURITY-AUDIT.$(date +%Y%m%d-%H%M%S).log"
fi
```

## Troubleshooting

**Hook not running?**
- Verify `.git/hooks/pre-push` exists and is executable: `ls -la .git/hooks/pre-push`
- Check symlink target if using option 1: `readlink .git/hooks/pre-push`
- Git version must be 1.8.2+; run `git --version`

**"Push blocked" but no reason in audit log?**
- Check `${AESOP_ROOT:-$HOME/aesop}/state/` directory exists and is writable.
- Verify git config: `git config user.name` is not empty.

**Secret scan warns but doesn't block?**
- `secret_scan.py` must exist at `AESOP_ROOT/tools/secret_scan.py`.
- If missing, the hook warns but allows push (fail-open default).
- For fail-closed behavior, edit the hook and remove the early `return 0`.

## References

- Hook source: `hooks/pre-push-policy.sh`
- Secret scanner: `tools/secret_scan.py`
- Audit log location: `state/SECURITY-AUDIT.log` (git-ignored)
- Cardinal rule: [CARDINAL-RULES.md](CARDINAL-RULES.md)

## Claude Code hooks

Beyond git hooks, aesop ships policy for the agent harness itself.
`hooks/claude/force-model-policy.mjs` is a Claude Code **PreToolUse** hook that
enforces the "subagents are always Haiku" cardinal rule in code: every `Agent`
or `Task` dispatch whose `model` is absent or non-compliant is rewritten to the
policy model before the subagent launches. Versioned in git, it is org policy
you can review, diff, and test — not a memo agents can forget.

### What it enforces

- **Model policy**: subagent dispatches run on `haiku` by default. If
  `aesop.config.json` defines `cardinal_rules.subagent_model` (looked up in
  `$AESOP_ROOT`, then the working directory), that model is enforced instead.
- **Rewrite, not block**: non-compliant dispatches are allowed through with
  `model` rewritten via the hook output contract
  (`hookSpecificOutput.updatedInput`), so work proceeds at the right cost tier.
- **Fail-open reliability**: malformed input produces no output and exit 0.
  The hook never crashes the harness and never logs payload contents.

### Registration (settings.json)

Add to `.claude/settings.json` in the project (or `~/.claude/settings.json`
for user-wide enforcement):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Agent|Task",
        "hooks": [
          {
            "type": "command",
            "command": "node \"$CLAUDE_PROJECT_DIR/hooks/claude/force-model-policy.mjs\""
          }
        ]
      }
    ]
  }
}
```

If the hook lives outside the project (e.g., a shared policy checkout), use an
absolute path to the `.mjs` file instead of `$CLAUDE_PROJECT_DIR`.

### Escape hatch

For dispatches that genuinely need a bigger model, include the literal marker
`[[ALLOW-NON-HAIKU]]` anywhere in the subagent prompt. The hook passes the
dispatch through untouched — and because the marker sits in the prompt, every
opt-out is visible in the transcript and auditable after the fact.

### Testing

```bash
node --import ./tests/helpers/isolated-env.mjs --test tests/force-model-policy.test.mjs
```

Covers the rewrite, escape-hatch, malformed-stdin, and config-override cases.
