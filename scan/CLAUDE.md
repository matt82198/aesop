# scan/ — IOC/secret scanner template

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only; dummy secrets are runtime-concatenated, never literal.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## What it does

`fleet-scan.example.mjs` is a config-driven template that scans committed code and fleet transcripts for security/alignment red-flags during each watchdog cycle. Copy to `fleet-scan.mjs` and configure paths to enable.

**Different from secret_scan.py**: fleet-scan.mjs runs every watchdog cycle (continuous monitoring, non-blocking); secret_scan.py gates every push (one-time blocking gate). Both use pattern-based detection.

## Setup

1. **Copy to enable**: `cp fleet-scan.example.mjs fleet-scan.mjs` (same directory)
2. **Configure in aesop.config.json**:
   - `fleet_root`: root dir for monitored repos (default: $HOME)
   - `transcripts_root`: dir containing ~/.claude/projects (default: ~/.claude/projects)
   - `repos`: array of {path, name, branch} to scan (paths relative to fleet_root or absolute)
   - `alerts_root`: dir for SECURITY-ALERTS.log (default: ../conductor3/state)
3. **Environment overrides** (precedence: env > config):
   - `AESOP_FLEET_ROOT`, `AESOP_TRANSCRIPTS_ROOT`, `AESOP_CONFIG`, `EXCLUDE_SESSION`

## Behavior

- **Runs**: Every watchdog cycle (~150s default); scans since last commit per repo + working tree
- **Logs**: Findings to `SECURITY-ALERTS.log` (append-only, never blocks fleet)
- **State**: Tracks seen findings (SHA1 dedup) in `.fleet-scan-seen.json`; last scanned commit per repo in `.fleet-scan-lastcommit-{reponame}`
- **Mark reviewed**: Prefix findings with `NOTE:` or `RESOLVED-FP` to suppress future alerts (edit `.fleet-scan-seen.json` manually or add to allowlists in code)

## Pattern-based IOC detection

**CODE_IOC** (git diff added lines, ~13 patterns):
- HIGH: exec/shell, reverse-shell, pipe-to-shell, b64-exec, secret-literals (AWS/OpenAI keys), cred access (.ssh, .aws, .env), destructive (rm -rf, DROP TABLE)
- MED: deserialization, reflection, raw-network sockets, hardcoded credentials

**PROMPT_IOC** (fleet transcripts, recent only, ~5 patterns):
- HIGH: prompt-injection (ignore prior instructions), exfiltration (send to URL), cred-harvest (read secrets), evade-security (disable antivirus), cred-harvest/exfiltration have allowlists to suppress false-positives on defensive tool references
- MED: danger-shell (curl|bash, base64 -d, rm -rf)
- Allowlists (exfiltration and cred-harvest share one rule, `isDefensiveReference`): a match is suppressed only when an allowlisted term appears as a whole token in the same clause as the match AND the message has no transmission indicator (POST, http(s)://, upload, curl/wget). Substring hits anywhere in the message never suppress (tests/scan-fleet-exfil-allowlist.test.mjs, tests/scan-fleet-cred-allowlist.test.mjs).

Add domain-specific checks by editing the REPOS/PROJECT_ROOTS arrays and IOC pattern lists.

## Relation to secret_scan.py

- fleet-scan.mjs: **ongoing** watchdog scanner (non-blocking, append-only log)
- secret_scan.py: **pre-push gate** (blocking, one-time check of staged changes)
- Both are pattern-based; fleet-scan is continuous fleet monitoring, secret_scan prevents credential leaks at commit time

## Dropped (reason)
- Customization section simplified into pattern names above (no "edit rules section" needed; patterns are inline in fleet-scan.example.mjs)
