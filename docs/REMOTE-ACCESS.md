# Remote Command Dispatch via GitHub Issues

**Purpose**: Enable dispatching orchestrator work from your phone, anywhere, without exposing the local machine to the internet. Comments on a designated GitHub issue are polled by a scheduled task and converted into queued orchestrator commands.

## Quick Start

### 1. Create a Control Issue

Create a **public** (or private) GitHub issue in `matt82198/aesop` as a control channel. Example:

```
Title: [REMOTE] Orchestrator Command Inbox
Body: 
This issue is a secure remote control channel for orchestrator dispatch.
Comments from @matt82198 are automatically polled and queued as commands.
```

**Note the issue number** (e.g., issue #999). You will use this in the scheduled task.

### 2. Run the Poller as a Scheduled Task (Windows)

The poller should run once per interval (e.g., every 5 or 10 minutes), not as a continuous daemon. This survives reboots and keeps resource usage minimal.

#### Create a Windows Scheduled Task

Use PowerShell to create the task (or use Task Scheduler GUI):

```powershell
# Define task parameters
$TaskName = "AesopRemoteInboxPoller"
$TaskDescription = "Poll GitHub issue for remote orchestrator commands"
$IssueNumber = 999  # YOUR ISSUE NUMBER HERE

# Command to run (single poll via --once)
$Command = "C:\Python312\python.exe"
$Arguments = "C:\Users\matt8\aesop\tools\remote_inbox.py --issue $IssueNumber --once"

# Create task trigger (every 5 minutes)
$Trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 365 * 100) -At (Get-Date) -Once

# Create task action
$Action = New-ScheduledTaskAction -Execute $Command -Argument $Arguments -WorkingDirectory "C:\Users\matt8\aesop"

# Create the task
Register-ScheduledTask -TaskName $TaskName -Trigger $Trigger -Action $Action -Description $TaskDescription -RunLevel Highest -Force
```

Or via Task Scheduler GUI:
1. Open Task Scheduler
2. Create Basic Task → `AesopRemoteInboxPoller`
3. Trigger: `Repeat every 5 minutes`
4. Action: Start a program
   - Program: `C:\Python312\python.exe`
   - Arguments: `C:\Users\matt8\aesop\tools\remote_inbox.py --issue 999 --once`
   - Start in: `C:\Users\matt8\aesop`

**Key points**:
- Use `--once` (single poll per run) — do NOT use `--loop` or continuous polling
- Schedule the task to run every 5–10 minutes
- Task should run under your user account (not SYSTEM)
- Python path must be explicit (no reliance on PATH)

### 3. Send Commands from Your Phone

Open the GitHub issue in your browser on your phone. Post a comment:

```
/runwave
```

or

```
/power
```

The poller will:
1. Detect your comment (within 5–10 minutes, depending on task frequency)
2. Verify you are the repo owner (by author login from GitHub API)
3. Queue the command to `~/conductor3/state/ui-inbox.md`
4. Post a reply acknowledging the command or explaining rejection

Your orchestrator will pick up the queued command on the next `/power` or session start.

## Allowlist

Only these **exact commands** are accepted and executed:

| Command | Purpose |
|---------|---------|
| `/runwave` | Start a new wave cycle |
| `/loopwaves` | Run multiple wave cycles in sequence |
| `/refinesystem` | Run refinement/hardening audit |
| `/refactor` | Run refactoring pass |
| `/recency` | Reconcile stale state |
| `/highvelocity` | Maximum parallelism mode |
| `/afk` | Autonomous mode (no prompts) |
| `/power` | Prime the orchestrator session |

**Any other text** (non-allowlisted commands, free text notes, questions) is automatically filed as a `NOTE` in the inbox and is **never executed** — only logged and acknowledged.

Examples:
- `/unknown do something` → Filed as NOTE, not executed ✅ (safe)
- `Please run /runwave when you get a chance` → Filed as NOTE ✅ (safe)
- `/power` → Executed as `/power` command ✅ (allowed)

## Security & Design

### Threat Model

**What this protects against**:
- **Phone loss**: Attacker reading/posting to an open GitHub issue cannot run arbitrary code
- **Network sniffing**: Only GitHub API calls are made (HTTPS); no credentials exposed locally
- **Local machine theft**: The machine runs no listening ports; only outbound polling
- **Replay attacks**: Comment IDs are tracked; the same command replayed by Git history cannot run twice

**What this does NOT protect against**:
- **GitHub account compromise**: An attacker with your GitHub credentials can post commands
- **Private repos**: The control issue must be in a public or private repo you own; if private, credentials must be secure
- **Injection via allowlist commands**: `/power` and `/runwave` are orchestrator commands; if you do not trust the orchestrator, do not use them

### How It Works

1. **Outbound polling only**: The machine opens NO inbound ports. It only makes outbound HTTPS calls to GitHub API via `gh` (already authenticated locally)
2. **Author verification**: Every comment is checked against the GitHub API response (not comment text). Only comments from `matt82198` are accepted
3. **Strict allowlist**: Only 8 commands are allowed. Anything else becomes a NOTE
4. **Idempotency**: Comment IDs are tracked in `~conductor3/state/.remote-inbox-seen`. A restart cannot replay an old comment
5. **Audit log**: Every action (accepted/rejected) is logged to `~/conductor3/state/REMOTE-DISPATCH.log` with timestamp and author

### Why Not Listen to Webhooks?

Webhooks would require an inbound port on your machine, exposing it to the internet. Polling is slower (5–10 minute latency) but keeps the machine private and rebootable.

### Why Not Use Email or SMS?

GitHub issues are:
- Integrated with your existing auth (no new credentials)
- Auditable (full history visible)
- Permissioned (private or public per repo)
- Device-agnostic (web, mobile, CLI)

## Troubleshooting

### "REJECT comment: author not repo owner"

**Cause**: You posted the comment from a different GitHub account.

**Fix**: Post from `matt82198` account, or update `tools/remote_inbox.py` line ~190 to match your owner login.

### No reply comment posted

**Cause**: The `gh` command may not have permission to post comments, or the task failed.

**Check**:
1. Verify `gh` is installed: `gh --version`
2. Verify authentication: `gh auth status`
3. Check scheduled task logs (Event Viewer → Windows Logs → Application)
4. Check `~/conductor3/state/REMOTE-DISPATCH.log` for error messages

### Command queued but not executed

**Cause**: Orchestrator has not read the inbox yet.

**Fix**: Explicitly run `/power` to prime the orchestrator, or wait for the next session start.

### "State directory not found"

**Cause**: `~/conductor3/state/` does not exist.

**Fix**: Ensure `conductor3` is initialized: `mkdir -p ~/conductor3/state`

## Files & Paths

| File | Purpose |
|------|---------|
| `tools/remote_inbox.py` | Poller script (run via scheduled task) |
| `tests/test_remote_inbox.py` | Comprehensive test suite |
| `~/conductor3/state/ui-inbox.md` | Command queue (appended by poller, read by orchestrator) |
| `~/conductor3/state/.remote-inbox-seen` | Seen comment IDs (idempotence tracker) |
| `~/conductor3/state/REMOTE-DISPATCH.log` | Audit log (all accepted/rejected commands) |

## Testing

### Test Locally

```bash
# Dry-run: parse comments, report what would happen, append nothing
python tools/remote_inbox.py --issue 999 --dry-run

# Single poll: process new comments
python tools/remote_inbox.py --issue 999 --once
```

### Run Test Suite

```bash
pytest tests/test_remote_inbox.py -v
```

Tests cover:
- Non-owner comment rejection ✅
- Non-allowlisted command filing as NOTE ✅
- Replay prevention ✅
- Valid command appending in correct format ✅
- gh API failure handling ✅
- Security (no arbitrary code execution) ✅

## Advanced: Polling Interval

The task scheduler runs every 5 minutes by default. You can adjust:

**More frequent (1 minute)**:
```powershell
$Trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 1) ...
```

**Less frequent (15 minutes)**:
```powershell
$Trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 15) ...
```

Faster polling = lower latency (commands execute faster) but higher GitHub API usage.

## Logs & Monitoring

### View Audit Log

```bash
tail ~/conductor3/state/REMOTE-DISPATCH.log

# Example output:
[2026-07-31T12:34:56.123456] ACCEPT     comment=1234567890 author=matt82198 command=/power
[2026-07-31T12:39:12.654321] ACCEPT     comment=1234567891 author=matt82198 command=NONE         filed as NOTE
[2026-07-31T12:44:00.000000] REJECT     comment=1234567892 author=hacker         command=NONE         author not owner
```

### Check Inbox Queue

```bash
python ~/scripts/inbox_drain.py pending

# Example:
[2026-07-31T12:34:56.123456] /power
[2026-07-31T12:39:12.654321] NOTE: please run refactor when ready
```

## Related

- **Main orchestrator documentation**: `docs/INSTALL.md`, `docs/ARCHITECTURE.md`
- **Inbox format & drain script**: `~/scripts/inbox_drain.py`
- **UI Inbox web submission**: `ui/web/components/SubmitForm.tsx`
- **Orchestrator integration**: `driver/wave_loop.py` (reads inbox on `/power`)
