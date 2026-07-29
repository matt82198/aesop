# Aesop Adoption Checklist

**Purpose**: Step-by-step acceptance criteria for adopting Aesop in a new repository. Each step names the exact command and the CI gate or tool that must pass.

---

## Prerequisites Verification

**Acceptance Criteria**: All required tools are installed and on PATH.

### Step 1.1: Check Claude Code CLI
```bash
claude --version
```
**Required Gate**: Output shows `Claude Code vX.Y.Z` (v0.1 or higher)
**CI Tool**: N/A (manual verification)

### Step 1.2: Check Git
```bash
git --version
```
**Required Gate**: Output shows `git version 2.40.0` or higher
**CI Tool**: N/A (manual verification)

### Step 1.3: Check Bash
```bash
bash --version
```
**Required Gate**: Output shows Bash 4+ (GNU or compatible)
**CI Tool**: N/A (manual verification)

### Step 1.4: Check Node.js
```bash
node --version
```
**Required Gate**: Output shows v18.0.0 or higher
**CI Tool**: N/A (manual verification)

### Step 1.5: Check Python
```bash
python3 --version
```
**Required Gate**: Output shows Python 3.10 or higher
**CI Tool**: N/A (manual verification)

---

## Scaffold Installation

**Acceptance Criteria**: Aesop harness created with all directories, hooks, and configuration templates.

### Step 2.1: Scaffold the Harness
```bash
npx @matt82198/aesop my-fleet \
  --name "my-project" \
  --repos "/path/to/my-repo"
```
**Required Gate**: Directory `my-fleet/` created with subdirectories (daemons/, skills/, ui/, tools/, etc.)
**CI Tool**: File existence check — verify these exist:
- `my-fleet/daemons/run-watchdog.sh`
- `my-fleet/skills/power/SKILL.md`
- `my-fleet/skills/buildsystem/SKILL.md`
- `my-fleet/hooks/pre-push-policy.sh`
- `my-fleet/aesop.config.example.json`

**Manual Verification**:
```bash
ls -la my-fleet/daemons/
ls -la my-fleet/skills/
ls -la my-fleet/hooks/
```

---

## Skills Installation

**Acceptance Criteria**: Orchestration skills registered in Claude Code home directory.

### Step 3.1: Copy /power Skill
```bash
cp -r my-fleet/skills/power ~/.claude/skills/power/
```
**Required Gate**: File exists at `~/.claude/skills/power/SKILL.md`
**CI Tool**: File existence check + SHA hash verification (identical copy)

### Step 3.2: Copy /buildsystem Skill
```bash
cp -r my-fleet/skills/buildsystem ~/.claude/skills/buildsystem/
```
**Required Gate**: File exists at `~/.claude/skills/buildsystem/SKILL.md`
**CI Tool**: File existence check + SHA hash verification (identical copy)

### Step 3.3: Verify Skills Installed
```bash
ls -la ~/.claude/skills/power/SKILL.md
ls -la ~/.claude/skills/buildsystem/SKILL.md
```
**Required Gate**: Both files exist and are readable
**CI Tool**: Stat + readable check; exit 1 if missing

---

## Configuration

**Acceptance Criteria**: Aesop configuration created and validated against schema.

### Step 4.1: Create Configuration File
```bash
cd my-fleet
cp aesop.config.example.json aesop.config.json
```
**Required Gate**: File `my-fleet/aesop.config.json` created and readable
**CI Tool**: File existence check

### Step 4.2: Edit Configuration
Edit `my-fleet/aesop.config.json` with your settings:
```json
{
  "backend": "claude",
  "aesop_root": "/path/to/my-fleet",
  "brain_root": "~/.claude",
  "repos": [
    {
      "path": "/path/to/my-repo",
      "name": "my-project"
    }
  ],
  "dashboard": {
    "port": 8770
  }
}
```
**Required Gate**: Valid JSON; all required keys present
**CI Tool**: `python tools/self_stats.py` or similar schema validator (stdlib json.load)

### Step 4.3: Validate Configuration
```bash
cd my-fleet
python -c "import json; json.load(open('aesop.config.json'))"
```
**Required Gate**: No JSON parse errors; exit 0
**CI Tool**: JSON schema validation; exit 1 if invalid

---

## Pre-Push Hook Installation & Verification

**Acceptance Criteria**: Git pre-push hook installed and verified green.

### Step 5.1: Install Pre-Push Hook
```bash
mkdir -p my-fleet/.git/hooks
cp my-fleet/hooks/pre-push-policy.sh my-fleet/.git/hooks/pre-push
chmod +x my-fleet/.git/hooks/pre-push
```
**Required Gate**: File exists at `my-fleet/.git/hooks/pre-push` with executable permission
**CI Tool**: Stat check + executable bit verification

### Step 5.2: Verify Hook Installation
```bash
bash -n my-fleet/.git/hooks/pre-push
```
**Required Gate**: Script syntax is valid; exit 0
**CI Tool**: Bash syntax check (`bash -n`); exit 1 if syntax error

### Step 5.3: Run Health Score Check
```bash
cd my-fleet
python tools/health_score.py
```
**Required Gate**: Score ≥ 50/100; all critical checks pass (PASS status)
**CI Tool**: `python tools/health_score.py --json` for machine parsing; verify:
- `git_hooks_installed` = true
- `config_valid` = true
- `critical_pass_count` > 0

**Expected Output**:
```
Health Score: 75/100
Critical: ✓ git hooks installed
Critical: ✓ config valid
Optional: ✓ state directory writable
Optional: ✗ daemon heartbeat (first run)
```

### Step 5.4: Run Secret-Scan Selftest
```bash
cd my-fleet
python tools/scanner_selftest.py
```
**Required Gate**: All pattern detections pass; exit 0
**CI Tool**: Regex pattern validation suite; exit 1 if any patterns fail

---

## First Wave Dry-Run

**Acceptance Criteria**: Orchestration system verified end-to-end with a single-turn wave.

### Step 6.1: Prime Orchestrator Brain
```bash
cd my-fleet
/power
```
**Required Gate**: State files created:
- `state/STATE.md` exists and is readable
- `state/BUILDLOG.md` exists and is readable
- `state/.watchdog-heartbeat` exists (timestamp format)
**CI Tool**: File existence + timestamp staleness check (<300s old)

**Expected Output**:
```
✓ Aesop brain primed
  - STATE.md initialized
  - BUILDLOG.md created
  - Watchdog heartbeat registered
```

### Step 6.2: Run One-Turn Wave (Dry-Run)
```bash
cd my-fleet
python driver/wave_loop.py --manifest wave.json --one-turn
```
**Required Gate**: Wave completes with exit code 0; output JSON contains:
- `"phase": "complete"` (or similar terminal state)
- `"agents_dispatched": N` (N ≥ 1)
- `"items_succeeded": M` (M ≥ 0; no error state)
**CI Tool**: JSON output parsing; verify schema + no fatal errors; exit 1 if any phase reports failure

**Expected Output**:
```
[wave] starting one-turn cycle...
[phase 1] backlog review complete
[phase 2] dispatching 4 parallel agents...
[agent 1] ✓ task complete
[agent 2] ✓ task complete
[agent 3] ✓ task complete
[agent 4] ✓ task complete
[phase 3] merge train complete (3 merged)
[phase 4] checkpoint written
[phase 5] audit complete
✓ wave complete: 4 agents, 3 shipped, 0 failures
```

### Step 6.3: Verify Wave Telemetry
```bash
cd my-fleet
python tools/wave_scorecard.py --json
```
**Required Gate**: JSON contains:
- `"items_dispatched": N` (≥ 1)
- `"items_succeeded": M` (M ≤ N)
- No `"error"` key
**CI Tool**: Schema + completeness check; verify cost ledger present; exit 1 if incomplete

---

## Optional: Dashboard Verification

**Acceptance Criteria**: Web dashboard launches and displays fleet status.

### Step 7.1: Launch Dashboard
```bash
cd my-fleet
npx @matt82198/aesop dash
```
**Required Gate**: Dashboard process starts; listens on port 8770; responds to HTTP GET /health
**CI Tool**: HTTP health check; `curl -s http://localhost:8770/health` returns JSON with `"status": "ok"`

### Step 7.2: Verify Dashboard Port
```bash
lsof -i :8770  # macOS/Linux
netstat -ano | grep 8770  # Windows
```
**Required Gate**: Process listening on port 8770 (python ui/serve.py)
**CI Tool**: Port binding check; exit 1 if unbound or different process

---

## Handoff Checklist

After completing all steps above, verify:

- [ ] Prerequisites verified (Claude Code, Git, Bash, Node, Python all ≥ minimum versions)
- [ ] Scaffold created (`my-fleet/` directory exists with all subdirectories)
- [ ] Skills copied to `~/.claude/skills/power` and `~/.claude/skills/buildsystem`
- [ ] Configuration created and valid JSON (`aesop.config.json` exists, `python -c "import json; json.load(open(...))"` passes)
- [ ] Pre-push hook installed at `my-fleet/.git/hooks/pre-push` (executable, syntax valid)
- [ ] Health score ≥ 50/100 (`python tools/health_score.py`)
- [ ] Secret-scan selftest passes (`python tools/scanner_selftest.py` exit 0)
- [ ] Orchestrator primed (`/power` completes, `state/STATE.md` exists)
- [ ] One-turn wave succeeds (`python driver/wave_loop.py --one-turn` exit 0, ≥1 agents dispatched)
- [ ] Wave scorecard generated (`python tools/wave_scorecard.py --json` valid, ≥1 items)
- [ ] Dashboard optional (if needed: `npx @matt82198/aesop dash` starts on port 8770)

**Adoption Complete**: All boxes checked = Aesop is ready for production orchestration on your repo.

---

## Troubleshooting Reference

If any step fails, see [PORTING.md](PORTING.md) for the 10 likeliest failure modes with symptoms, causes, and fixes:
1. Secret-scan blocks legit push (test fixtures)
2. Worktree isolation violated (shared .git/index)
3. Heartbeat stale/missing (permissions)
4. Port 8770 conflict (other service)
5. Git identity placeholder (config defaults)
6. CRLF line endings (Windows autocrlf)
7. Test count drift in CI (baseline mismatch)
8. UTF-8 encoding on Windows (cp1252 vs UTF-8)
9. Cost ceiling never triggers (spend uncapped)
10. State-store SQLite locked (concurrent access)

See also:
- [INSTALL.md](INSTALL.md) — setup for new harness directory
- [ANY-REPO.md](ANY-REPO.md) — walkthrough for any repository
- [CONFIGURE.md](CONFIGURE.md) — configuration reference
- [HOOK-INSTALL.md](HOOK-INSTALL.md) — pre-push hook details
