# Cross-Machine Reconstitution — Restore Aesop & Fleet After a Wipe

**Purpose**: Aesop is designed so that compute is disposable. The brain (rules, memory, configuration) and fleet work (git commits, backups) live in remotes. This playbook describes how to reconstitute a full fleet on a new machine from scratch using only git and the aesop harness.

---

## 1. Before a Wipe: Continuous Posture

**What the watchdog guarantees** (if running continuously):

The watchdog daemon (`daemons/run-watchdog.sh`) runs every 150 seconds and:
- **Discovers fleet repos** automatically by scanning `~/.* ~/* ~/dev/*` (dot-directories in home, home root, dev subtree)
- **Backs up uncommitted work** as ephemeral commits to `backup/wip-YYYYMMDD` branches (named by calendar date)
- **Backs up unpushed commits** on `master`/`main` to `backup/master-wip-YYYYMMDD` branches
- **Pushes feature branches** normally to origin
- **Scans for secrets** before every push (via `tools/secret_scan.py`); blocks if credentials are detected
- **Writes heartbeat** to `state/.watchdog-heartbeat` (epoch seconds; used by the watchdog itself to avoid duplicate runs)
- **Logs all actions** to `state/FLEET-BACKUP.log` (append-only; persists across sessions)
- **Records repo state** to `state/.watchdog-repos.json` (which repos were touched, what state: SNAPSHOTTED/PUSHED/BLOCKED/CLEAN)

**Verify your current posture right now**:

```bash
# 1. Check watchdog heartbeat age (should be <200s if daemon is alive)
stat state/.watchdog-heartbeat
# On Unix: check "Modify" timestamp
# On Windows: check "Last Write Time"

# 2. Tail the backup log to see last cycle
tail -20 state/FLEET-BACKUP.log

# 3. Check which repos were touched in the last cycle
cat state/.watchdog-repos.json | jq '.' # (requires jq, or use any JSON viewer)
```

**What is NOT backed up automatically**:
- Heartbeat files, logs, and JSON status are transient (in `.gitignore`) — useful for live monitoring but not recovered after a wipe.
- `aesop.config.json` (holds your local paths, credentials, custom settings) is git-ignored for security — you must recreate it.
- The private brain directory (`~/.claude/`) lives in a separate private remote, not in this public aesop repo.

---

## 2. After a Wipe: Reconstitute the Fleet

### Step 1: Clone Aesop

```bash
git clone <aesop-remote-url> ~/aesop
cd ~/aesop
```

Replace `<aesop-remote-url>` with your remote (e.g., `https://github.com/your-org/aesop` or `git@github.com:your-org/aesop.git`).

### Step 2: Restore the Private Brain

The team brain lives in a separate private remote (not in the aesop repo). Clone it early so rules and memory prime your session:

```bash
git clone <brain-remote-url> ~/.claude
```

This populates:
- `~/.claude/CLAUDE.md` — your team's cardinal rules and agent governance
- `~/.claude/MEMORY.md` or individual fact files in `~/.claude/memory/` — your team knowledge
- `~/.claude/docs/` — architecture guides, dispatch patterns, etc.

Without the brain, Claude Code sessions will lack your org's policies and context.

### Step 3: Create Local Configuration

```bash
cd ~/aesop
cp aesop.config.example.json aesop.config.json
```

Edit `aesop.config.json` to match your setup:
- `aesop_root`: path to your aesop installation (e.g., `/home/user/aesop` or `$HOME/aesop`)
- `brain_root`: path to your brain (e.g., `$HOME/.claude`)
- `scripts_root`: path to reusable scripts (e.g., `$HOME/scripts`)
- `temp_root`: temporary scratch directory (e.g., `/tmp` or `$env:TEMP` on Windows)
- `repos[]`: list of fleet repos (path, name, primary_branch, backup_branch)
- `watchdog.cycle_seconds`: how often the daemon runs (default 150)
- `watchdog.heartbeat_threshold_seconds`: how long before heartbeat is considered stale (default 200)

### Step 4: Create Required Directories

```bash
mkdir -p ~/aesop/state
mkdir -p ~/.claude/memory  # If not already created by brain clone
```

The `state/` directory will be populated by the watchdog at runtime.

### Step 5: Set Environment Variables (Optional but Recommended)

```bash
# Bash/Unix
export AESOP_ROOT=$HOME/aesop

# PowerShell/Windows
$env:AESOP_ROOT = "$env:USERPROFILE\aesop"
```

The scripts check `AESOP_ROOT` and default to `.` (current directory) if unset. Setting it explicitly avoids confusion.

### Step 6: Verify the Watchdog

Run the watchdog in single-shot mode to verify the setup:

```bash
bash $AESOP_ROOT/daemons/run-watchdog.sh --once
```

Expected output: logs to `state/FLEET-BACKUP.log` showing which repos it discovered and their states (CLEAN, SNAPSHOTTED, PUSHED, or BLOCKED).

### Step 7: Clone Fleet Repos Using Reconstitute

The aesop harness includes a bootstrap tool to clone and fetch all your fleet repos automatically. This is the recommended approach for reconstituting a full fleet:

#### Option A: Using aesop.config.json (Recommended)

If your `aesop.config.json` includes a `repos` array with `url` and `path` fields, run:

```bash
bash $AESOP_ROOT/tools/reconstitute.sh
```

This will clone any missing repos and fetch existing ones, printing a summary of cloned, fetched, and failed repos.

#### Option B: Using a repos file

Create a text file with one repo per line in the format `<url> <target-dir>` or `<url>⇥<target-dir>` (tab-delimited):

```
https://github.com/user/project1.git ~/project1
https://github.com/user/project2.git ~/project2
```

Then run:

```bash
bash $AESOP_ROOT/tools/reconstitute.sh --repos-file repos.txt
```

#### Dry-run mode (preview without executing)

```bash
bash $AESOP_ROOT/tools/reconstitute.sh --dry-run
```

#### Manual fallback

If you prefer to clone repos manually or need fine-grained control, you can still clone individually:

```bash
# For each fleet repo, clone from its remote
git clone <repo-url> <local-path>

# Example: if repos are at ~/project1, ~/project2, etc.
cd ~/project1 && git fetch -q origin
cd ~/project2 && git fetch -q origin
```

### Step 8: Recover Snapshotted Uncommitted Work

The watchdog pushed uncommitted work to `backup/wip-YYYYMMDD` branches. To recover:

```bash
# In each repo, check for backup branches
git branch -r | grep backup

# Example: backup/wip-20260712 exists
# Inspect what was snapshotted
git log --oneline origin/backup/wip-20260712 -5

# RECOMMENDED: Cherry-pick specific commits (safe, adds commits on top)
git cherry-pick origin/backup/wip-20260712

# WARNING: Only use reset if you will NOT do Step 9 after this step.
# Resetting here will be overwritten by Step 9's reset, losing work.
# (Use reset only if you're certain you want to replace your entire branch
# and have no unpushed commits to recover in Step 9)
# git reset --hard origin/backup/wip-20260712
```

### Step 9: Recover Snapshotted Unpushed Commits on Main/Master

If you had unpushed commits on your default branch, the watchdog pushed them to `backup/master-wip-YYYYMMDD`:

```bash
# Check for master/main backup branches
git branch -r | grep backup.*master

# Example: backup/master-wip-20260712 exists
# Inspect the commits
git log --oneline origin/backup/master-wip-20260712 -10

# Reapply them to your main branch
git reset --hard origin/backup/master-wip-20260712
git push origin HEAD:main  # or 'master' if that's your primary branch
```

### Step 10: Restart the Daemon

Once your fleet is restored and verified, restart the watchdog in daemon mode:

```bash
bash $AESOP_ROOT/daemons/run-watchdog.sh &
```

Or, if using your own process manager (systemd, supervisor, etc.), start it according to your setup.

The watchdog will now run every 150 seconds, backing up any new changes and pushing them to origin.

---

## 3. Recovering the Private Brain

The brain directory (`~/.claude/`) is the single point of truth for your org's rules, memory, and agent behavior. It lives in a **separate private remote** (not in the public aesop repo) so credentials and private facts never leak.

### Clone the Brain

Before or alongside restoring aesop, clone your brain:

```bash
git clone <your-private-brain-remote> ~/.claude
```

### Brain Contents

Once cloned, you have:

**CLAUDE.md** — Your team's version of the cardinal rules, tailored to your org:
- Which domains your team works on (e.g., "infrastructure," "security," "data")
- Org-specific policies (e.g., approval chains, secret-scan rules, cost limits)
- Escalation paths and decision-makers
- Reusable tool policies (e.g., "AWS CLI always requires approval")

**MEMORY.md or `memory/` fact files** — Long-lived team knowledge:
- Team names, email addresses, and roles
- Past decisions and their rationales (searchable history)
- Known limitations and workarounds
- Approved vendors, tool licenses, cost budgets

**docs/** — Org-specific architecture and tutorials:
- Dispatch patterns that work well for your team
- Performance characteristics and lessons learned
- Custom monitoring and alerting playbooks

### Why It's Separate

The brain is in a private repo because:
- It may contain credentials (API keys for dashboards, auth tokens for internal services).
- It may reference internal team facts (email addresses, org structure) that are not public.
- It evolves independently of the aesop harness itself.

**Do NOT** put the brain in the public aesop repo. Clone it from your private remote on every fresh machine.

---

## 4. Drill: Test Recovery on a Second Machine or VM

To verify this playbook works end-to-end, run a recovery on a clean machine (or a new VM):

### Prerequisites

- Git is installed and configured with access to your remotes (SSH keys or credentials available).
- Bash is available (Git Bash on Windows, bash on Unix).
- Python 3.11+ is available (for secret-scan, optional for testing the watchdog).
- Node.js 18+ is available (optional; dashboard requires it but is not essential for the watchdog).

### Drill Steps

```bash
# 1. Start with a clean home directory (or VM)
# Simulate a wipe by deleting ~/aesop, ~/project1, ~/project2, etc.

# 2. Clone aesop
git clone <aesop-remote> ~/aesop
cd ~/aesop

# 3. Clone the brain
git clone <brain-remote> ~/.claude

# 4. Create config (edit paths to match the new machine)
cp aesop.config.example.json aesop.config.json
# Edit aesop.config.json with your local paths and repo list (including repos[].url)

# 5. Create state directory
mkdir -p ~/aesop/state

# 6. Clone fleet repos using reconstitute
bash $AESOP_ROOT/tools/reconstitute.sh

# 7. Run watchdog once
export AESOP_ROOT=$HOME/aesop
bash $AESOP_ROOT/daemons/run-watchdog.sh --once

# 8. Check the log
tail -30 $AESOP_ROOT/state/FLEET-BACKUP.log

# 9. Recover any snapshotted work from backup branches
cd ~/project1
git branch -r | grep backup
git log --oneline origin/backup/wip-* -5
# (cherry-pick or reset as needed)

# 10. Verify all work is recovered
git status
git log --oneline -10
```

### Expected Recovery Time

For a typical fleet (3–5 repos, average size 100MB–500MB):
- **Clone aesop + brain**: ~30 seconds
- **Create config + directories**: ~10 seconds
- **Clone fleet repos**: ~2–5 minutes (depends on size and network)
- **Run watchdog once**: ~30 seconds
- **Recover snapshotted work**: ~1–2 minutes (git operations)

**Total**: ~5–10 minutes start-to-finish.

For very large repos (>1GB) or slow networks, add 2–3 minutes per repo.

---

## 5. Known Limitations

### What This Playbook Does NOT Do

1. **Automatic repo discovery on clone (Solved by reconstitute.sh)**
   - The watchdog discovers repos when it runs, but only repos that already exist on disk.
   - Use `bash $AESOP_ROOT/tools/reconstitute.sh` to clone all repos from `aesop.config.json` automatically.
   - Reconstitute supports `--dry-run` for preview and `--repos-file` for custom repo lists.

2. **Recover transient state/ directory**
   - Heartbeat files (`.watchdog-heartbeat`), logs (`FLEET-BACKUP.log`), and JSON status (`.watchdog-repos.json`) are git-ignored.
   - They are recreated fresh when the watchdog runs, so you lose the history of what happened during the wipe.
   - Future enhancement: persist logs to a cloud service or central logging system.

3. **Recover aesop.config.json**
   - The config file is git-ignored for security (contains local paths, possibly credentials).
   - You must recreate it manually after a wipe.
   - Workaround: store a template or commented version in a separate private repo, or use a configuration management tool (Ansible, Terraform).

4. **Authenticate to private remotes**
   - Git credentials (SSH keys, personal access tokens) are machine-local and not backed up.
   - You must set up git authentication (SSH keys, git credential helper, or `~/.netrc`) on the new machine before cloning remotes.
   - This is a feature, not a bug: credentials must never leave the machine.

5. **Restore stashed work**
   - If you stashed uncommitted work in a repo (e.g., via `git stash`), it is NOT in the watchdog backups.
   - The watchdog only backs up uncommitted changes that are in the working directory or staging area at the time it runs.
   - Mitigation: always commit or snapshot work before a wipe; use `git stash pop` before a wipe to restore stashed work.

6. **Recover monitor state**
   - The orchestration monitor (`monitor/collect-signals.mjs`) writes `BRIEF.md`, `SIGNALS.json`, and `.monitor-heartbeat` files, which are git-ignored.
   - After a wipe, you start with a fresh slate; you lose the history of orchestration signals.
   - Future enhancement: persist monitor state to a cloud service.

7. **Recover custom dashboards or extensions**
   - If you've customized `dash/watchdog-gui.sh`, `ui/serve.py`, or added extensions to `tools/`, ensure they are committed to the repo.
   - Only committed code is restored; uncommitted customizations are lost.

### Mitigations

- **For critical uncommitted work**: Ensure the watchdog is always running so work is snapshotted every 150 seconds.
- **For credentials and config**: Use a separate private remote for `aesop.config.json` templates and credentials, or use a CI/CD system to inject them.
- **For large repos**: Consider using git shallow clones (`--depth 1`) or sparse checkouts to speed up recovery for disaster scenarios.
- **For compliance and audit trails**: Periodically archive `state/FLEET-BACKUP.log` to a central logging system so you can audit what happened after a wipe.

---

## Summary

| Phase | Action | Time |
|-------|--------|------|
| Clone | `git clone <aesop>` + `git clone <brain>` | ~1 min |
| Config | Create `aesop.config.json` + `mkdir state/` | ~2 min |
| Repos | `bash tools/reconstitute.sh` (clone + fetch) | ~2–5 min |
| Verify | `bash daemons/run-watchdog.sh --once` | ~1 min |
| Recover | `git cherry-pick` from `backup/wip-*` branches | ~1–2 min |
| Restart | `bash daemons/run-watchdog.sh &` | immediate |
| **Total** | | **~7–12 min** |

The watchdog, combined with continuous git discipline, ensures that **compute is disposable but work is durable**. A machine wipe is annoying but not catastrophic.

---

## References

- `README.md` — installation and quick-start guide
- `CARDINAL-RULES.md` — foundational principles (rule #5: "durable state")
- `daemons/run-watchdog.sh` — watchdog daemon implementation
- `daemons/backup-fleet.sh` — backup and push logic
- `.gitignore` — what is and isn't persisted across wipes
