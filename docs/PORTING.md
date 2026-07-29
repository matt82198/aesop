# Porting Aesop to Your Repository

Guide for adopters porting the orchestration harness to a foreign repo. Step-by-step with prerequisites, scaffold, config, and the 10 likeliest failure modes from real deployments.

**Quick decision**:
- **New harness directory?** See [INSTALL.md](INSTALL.md) for the quick 5-minute scaffold.
- **Existing codebase?** You're in the right place. This guide covers integrating Aesop into your project with common pitfalls.

---

## Prerequisites

Ensure you have:
- **Node.js** ≥18 (from `package.json`, `.nvmrc`: v18.20.5)
- **Python** ≥3.10 (for daemons, health checks, secret-scan)
- **Git** ≥2.40 (worktree + pre-push hooks)
- **Bash** v4+ (or Git Bash on Windows)
- **Optional**: Playwright (for UI verification testing); jq (for dashboard JSON)

Check versions:
```bash
node --version && python3 --version && git --version && bash --version
```

### OS Notes
- **Windows**: Use Git Bash for all shell commands; paths use `/c/Users/...` POSIX style
- **Linux/macOS**: Standard Bash; ensure `/usr/bin/bash` or equivalent exists

---

## 1. Scaffold & Install

### Step 1: Create your harness
```bash
npx @matt82198/aesop my-fleet \
  --name "my-project" \
  --repos "/path/to/repo1,/path/to/repo2"
```

Creates `my-fleet/` with daemons, skills, config, and UI.

### Step 2: Copy skills to Claude Code home
```bash
cp -r my-fleet/skills/power ~/.claude/skills/power
cp -r my-fleet/skills/buildsystem ~/.claude/skills/buildsystem
```

### Step 3: Configure & test
Edit `my-fleet/aesop.config.json`:
```json
{
  "backend": "claude",
  "aesop_root": "/path/to/my-fleet",
  "brain_root": "~/.claude",
  "repos": [
    { "path": "/path/to/repo1", "name": "my-project" }
  ],
  "dashboardPort": 8770
}
```

Test dashboard backend (Ollama example):
```json
{
  "backend": "openai-compatible",
  "base_url": "http://localhost:11434/v1",
  "model": "mistral",
  "is_local": true
}
```

### Step 4: Install pre-push hook
```bash
mkdir -p my-fleet/.git/hooks
cp my-fleet/hooks/pre-push-policy.sh my-fleet/.git/hooks/pre-push
chmod +x my-fleet/.git/hooks/pre-push
```

### Step 5: Verify with health check
```bash
cd my-fleet
python tools/health_score.py
```
Expected output: All checks ✓; port 8770 available; git hooks installed.

---

## 2. First `/power`, First Wave

Run the orchestrator once to prime the brain:
```bash
cd my-fleet
/power
```
Expected output: State created (STATE.md, BUILDLOG.md, .watchdog-heartbeat).

Run one complete wave:
```bash
/buildsystem --one-turn
```
Expected verdicts: All items "PASS" or "REVIEW"; zero code defects. Health score should report ≥85/100.

---

## 3. The 10 Likeliest Failure Modes

Each: symptom → cause → fix.

### 1. Secret-scan blocks legit push
- **Symptom**: `git push` fails: "secret detected" but code has no real secret
- **Cause**: Test fixtures with dummy secrets (credit card, API key patterns) are parsed as real
- **Fix**: Assemble secrets at runtime: `"key" + "_" + "123"` not `"key_123"`; use `---` delimiters in comments. Secret-scan only scans staged files, not .git.

### 2. Worktree isolation violated
- **Symptom**: Editing feature branch X; changes also appear in primary tree
- **Cause**: Agents not using isolated worktrees; sharing same `.git/index`
- **Fix**: Each agent task must use `git worktree add ../wt-item-slug -b feature/item-slug origin/main`; verify with `git worktree list`

### 3. Heartbeat stale/missing
- **Symptom**: Watchdog won't start; error: "unreadable state dir" or "stale heartbeat"
- **Cause**: `state/.watchdog-heartbeat` missing/corrupted or directory unreadable (permissions)
- **Fix**: `rm -f state/.watchdog-heartbeat && bash daemons/run-watchdog.sh --once` (re-creates it)

### 4. Port 8770 conflict
- **Symptom**: `python ui/serve.py` fails: "Address already in use"
- **Cause**: Old dashboard process still bound, or another service using port 8770
- **Fix**: `lsof -i :8770 | grep -v PID | awk '{print $2}' | xargs kill -9` (Mac/Linux); Windows: `netstat -ano | grep 8770`, kill the PID. Or change `dashboardPort` in config.

### 5. Git identity placeholder
- **Symptom**: Pre-push hook fails: "user identity invalid"; commits fail
- **Cause**: Git config has template defaults (e.g., "John Doe") instead of your name
- **Fix**: `git config user.name "Your Name" && git config user.email "you@example.com"`

### 6. CRLF line endings corrupt scripts
- **Symptom**: Bash script fails: "command not found" at line 50 (but line 50 exists)
- **Cause**: Editor/Windows converted LF to CRLF; bash reads `\r` as part of command
- **Fix**: `git config core.autocrlf false` locally; convert file: `dos2unix daemons/run-watchdog.sh`

### 7. Test count drift in CI
- **Symptom**: Health-check fails: "expected 127 tests, got 128"
- **Cause**: Added new test file but didn't update baseline in tools/health_score.py or CI config
- **Fix**: Run `python tools/health_score.py --json` to get true count; update baseline in CI gate

### 8. UTF-8 encoding on Windows
- **Symptom**: Pre-publish gate fails with encoding error in secret_scan.py
- **Cause**: Python opens files in system encoding (cp1252) instead of UTF-8
- **Fix**: Aesop tools now force UTF-8 internally; ensure `PYTHONIOENCODING=utf-8` if running external Python scripts

### 9. Cost ceiling never triggers
- **Symptom**: Cost ceilings configured; spending uncapped; halt never fires
- **Cause**: Claude Code driver returns `get_tokens_spent()=None`; coerces to 0; ledger fallback skipped
- **Fix**: For Claude Code, cost tracking integrates live API; for non-Claude backends, ledger fallback logs at end of wave

### 10. Hook TTY behavior blocks CI pushes
- **Symptom**: `git push` fails in CI/cron: "cannot read stdin"; interactive prompt hangs
- **Cause**: Hook tries to detect TTY when stdin is `/dev/null` (non-interactive context)
- **Fix**: Hook now handles empty stdin (rc=2 for delete-only, skip scan + log); use `hooks/pre-push-policy.sh --test` in CI (no-op validation)

---

## Next Steps

1. **Read [INSTALL.md](./INSTALL.md)** — Full setup and environment variables
2. **Run [health_score.py](../tools/health_score.py)** — Continuous readiness checks
3. **Explore the [README](../README.md)** — Demo walkthrough and proof numbers
4. **For troubleshooting**, check [GOVERNANCE.md](./GOVERNANCE.md) or open an issue on GitHub

---

**License**: MIT License (open-source). See [LICENSE](../LICENSE) for details.
