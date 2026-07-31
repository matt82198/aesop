# Remote Fleet Observability

Reach your Aesop fleet status from anywhere — your phone on bad festival wifi, a coffee shop, or across the world — without exposing your local machine to the public internet.

## Publishing Snapshots

The `tools/status_publish.py` tool gathers a compact fleet-status snapshot and publishes it to a **secret GitHub gist** (private by default). A phone can then read that one page without needing direct access to your machine or exposing anything publicly.

### Quick Start

```bash
# Print a snapshot (verify the output first)
python tools/status_publish.py --dry-run

# Create a secret gist (one-time setup)
gh gist create --secret /dev/null
# Copy the gist ID from the output (example: a1b2c3d4e5f6g7h8)

# Publish once to your secret gist
python tools/status_publish.py --gist-id a1b2c3d4e5f6g7h8

# Add to aesop.config.json to make it default
# {
#   "status_publish_gist_id": "a1b2c3d4e5f6g7h8"
# }

# Then subsequent calls just need:
python tools/status_publish.py

# Set it to run automatically on a schedule (see below)
```

### As a Windows Scheduled Task

For reliable, reboot-surviving updates, run status_publish.py as a Windows Scheduled Task using the same durable pattern as the existing watchdog and monitor tasks.

#### Setup (PowerShell, Admin)

First, find your Python interpreter path:

```powershell
(Get-Command python).Source
# Example output: C:\Python314\python.exe
```

Then create the task (replace PYTHON_PATH with your output from above):

```powershell
# Create a task that runs every 15 minutes
$TaskName = "AesopStatusPublish"
$TaskPath = "\Aesop\"
$WorkDir = "C:\path\to\aesop"
$PythonPath = "C:\Python314\python.exe"  # UPDATE THIS: use (Get-Command python).Source

# Build the command: call Python with explicit interpreter, working directory, timeout
$Action = New-ScheduledTaskAction `
  -Execute $PythonPath `
  -Argument "tools\status_publish.py --once" `
  -WorkingDirectory $WorkDir

# Run every 15 minutes, indefinitely
$Trigger = New-ScheduledTaskTrigger `
  -RepetitionInterval (New-TimeSpan -Minutes 15) `
  -At (Get-Date) `
  -RepetitionDuration (New-TimeSpan -Days 36500)

# Suppress success output, log to file
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable

# Register the task
Register-ScheduledTask `
  -TaskName $TaskName `
  -TaskPath $TaskPath `
  -Action $Action `
  -Trigger $Trigger `
  -Settings $Settings `
  -Description "Publish Aesop fleet status snapshot to secret gist every 15 minutes"

# Verify
schtasks /query /tn "Aesop\$TaskName" /v
```

**Critical**: Always discover your Python path dynamically with `(Get-Command python).Source`. Do not hardcode paths like `C:\Program Files\Python312\python.exe` — they become silent failures when Python is installed elsewhere.

#### Manage the Task

```powershell
# View status
schtasks /query /tn "Aesop\AesopStatusPublish" /v

# Run now
schtasks /run /tn "Aesop\AesopStatusPublish"

# Disable (keep but don't run)
schtasks /change /tn "Aesop\AesopStatusPublish" /disable

# Remove
schtasks /delete /tn "Aesop\AesopStatusPublish" /f
```

### Configuration

Add to `aesop.config.json`:

```json
{
  "status_publish_gist_id": "your-secret-gist-id-here",
  "state_root": "/path/to/<fleet-state-dir>/state"
}
```

**Required**: `status_publish_gist_id` (create via `gh gist create --secret /dev/null`)

### Payload Contents

Each snapshot includes:

- **Live Status**: agents, open PRs with RED CI count, heartbeat freshness (MTIME-based, file-not-found is ERROR state)
- **Recent Activity**: last few lines from BUILDLOG.md
- **Pending Items**: unprocessed inbox submissions

Total: fits on a phone screen.

### Visibility & Redaction

**Visibility Check (FAIL-CLOSED)**:
- Queries gist privacy BEFORE publishing (`gh gist view --json isPublic`)
- REFUSES to publish to PUBLIC gists (aborts with clear error)
- Explicit gist-id required (no dangerous defaults)

**Redaction (Defense-in-Depth)**:
Before publishing, the payload is scanned for:

- API tokens (`sk-*`, `ghp-*`, `pat-*`)
- Local paths (`C:\Users\<user>\...`, `/home/<user>/...`)
- References to `<fleet-state-dir>` (local state directory)

**Redaction failure blocks publishing** (exit 1) rather than guessing which paths to redact. If more than 10% of content would be removed, the publish fails automatically. <!-- metrics-verified: tools/status_publish.py redact_payload() line 252 -->

Exit codes:

- `0` = published successfully or no changes since last publish
- `1` = publish failed (visibility check, redaction issue, gh error)
- `2` = fatal error (missing gist-id, malformed config, etc.)

### Idempotence

The tool tracks the last-published snapshot hash (in `state/.status-publish-last`). If the payload hasn't changed, the update is skipped — no noise, no wasted API calls.

Useful for frequent intervals: set up the scheduled task to run every 5 minutes, but GitHub is only updated when the status actually changes.

---

## Reaching the Full Dashboard Remotely

For more detailed observability than a snapshot allows, you can reach the full web dashboard (`localhost:8770`) from a remote device using a tunnel or private network.

### Option 1: Tailscale (RECOMMENDED)

**Why**: Secure private mesh network. Your phone and machine are on the same private network. No public URL, no firewall rules, nothing exposed. Survives network changes.

#### Setup

1. **Install Tailscale on your machine** (Windows):
   ```powershell
   # Download from https://tailscale.com/download
   # Or via Chocolatey:
   choco install tailscale
   
   # Start the client
   tailscale up
   ```

2. **Authenticate**:
   - A browser will open; sign in with your GitHub account (or email)
   - Approve this device to join your tailnet

3. **Install Tailscale on your phone**:
   - iOS App Store or Android Google Play: "Tailscale"
   - Open the app and sign in with the same account
   - The phone auto-joins your tailnet

4. **Access the dashboard**:
   - On your phone, open a browser and navigate to:
     ```
     http://[machine-tailscale-ip]:8770
     ```
   - Find your machine's Tailscale IP:
     ```bash
     tailscale ip -4
     ```
   - Example: `http://100.64.123.45:8770` (Tailscale IPs are always 100.x.y.z)

**Cost**: Free for personal use (up to 100 devices). Tailscale manages NAT traversal, no port forwarding needed, works across all networks.

**Security**: Encrypted end-to-end, private network only. Your phone never sees a public URL.

---

### Option 2: Cloudflare Tunnel + Access (Identity-Gated Public URL)

If you need more granular access control, Cloudflare Tunnel + Access provides a public URL gated by your Cloudflare account.

#### Setup

1. **Install Cloudflare Warp** (on machine):
   ```bash
   # Windows: Download from https://1.1.1.1/
   # macOS: brew install cloudflare/warp/warp
   # Linux: https://pkg.cloudflareclient.com/
   ```

2. **Create a Tunnel**:
   ```bash
   warp-cli tunnel login
   warp-cli tunnel create aesop-dashboard
   warp-cli tunnel route ip add 8770 aesop-dashboard
   warp-cli tunnel run
   ```

3. **Set up Access policy** (in Cloudflare dashboard):
   - Add an "Allow" rule requiring Cloudflare authentication
   - Or gate by email/group if using Cloudflare Teams

4. **Access from your phone**:
   - Visit the Cloudflare Tunnel URL (from your dashboard)
   - Authenticate via your Cloudflare account
   - Browse the dashboard

**Cost**: Paid (Cloudflare Teams or Warp+). More setup, but explicit identity gating.

**Security**: Public URL (requires authentication), encrypted tunnel.

---

### Option 3: ngrok (NOT RECOMMENDED for Control Surfaces)

ngrok provides a quick public URL via `ngrok http 8770`, but:

- **Exposes the dashboard to the internet** behind a generic URL
- **Risk**: The URL is discoverable; if leaked, anyone can access your fleet control surface
- **Not recommended**: The Aesop dashboard is a control surface (can influence the fleet), not just a viewer

Use only if:
- You understand the security trade-off
- The dashboard is read-only for your use case
- You are in a time-bound scenario (e.g., demo, testing)

---

## Recommendation Matrix

| Scenario | Recommended | Reasoning |
|----------|-------------|-----------|
| Private mesh, phone on road | **Tailscale** | No public URL, auto-encryption, hassle-free |
| Enterprise SSO required | Cloudflare Tunnel + Access | Identity policy control |
| Quick demo (time-bound) | ngrok | Fastest setup, but acknowledge public exposure |
| Permanent production | **Tailscale** | Only secure long-term option |

**Bottom line**: Use **Tailscale** for all production use. Use `status_publish.py` for lightweight phone observability on bad WiFi (primary use case). Reserve full dashboard access for Tailscale.

---

## Troubleshooting

### Missing Gist ID

If publish fails with "gist-id required":
- Create a secret gist: `gh gist create --secret /dev/null`
- Copy the ID from the output
- Pass it as `--gist-id ID` or add to `aesop.config.json`

### Redaction Failures

If publish fails with "Redaction would remove > 10%": <!-- metrics-verified: tools/status_publish.py redact_payload() -->
- Check for accidentally-committed secrets in BUILDLOG or pending items
- Inspect what `gather_buildlog_summary()` is pulling
- Use `--dry-run` to inspect the payload before publishing

### Visibility Check Failures

If publish fails with "gist is PUBLIC":
- Verify your gist is private: `gh gist view <id> --json isPublic`
- If public, delete it and create a new secret gist: `gh gist create --secret /dev/null`

### Scheduled Task Not Running

```powershell
# Check the task status
schtasks /query /tn "Aesop\AesopStatusPublish" /v

# Check the last run result
Get-ScheduledTaskInfo -TaskName "AesopStatusPublish" | Select LastRunTime, LastTaskResult

# View logs
Get-ScheduledTask -TaskName "AesopStatusPublish" | Get-ScheduledTaskInfo

# Re-run manually
schtasks /run /tn "Aesop\AesopStatusPublish" /v

# Common issue: Python path is wrong
# Solution: use (Get-Command python).Source to discover the correct path
```

### Tailscale Connectivity Issues

```bash
# Check your Tailscale status
tailscale status

# View your machine's Tailscale IP
tailscale ip -4

# Restart the Tailscale client
tailscale logout
tailscale up
```

---

## Security Principles

1. **Snapshots are private GitHub gists** — only accessible to your GitHub account
2. **Tailscale is encrypted end-to-end** — traffic never touches the public internet
3. **No public URLs** — dashboard is never exposed unauthenticated
4. **Redaction is mandatory** — never publish secrets; failure blocks the publish
5. **Scheduled tasks are local** — all work runs on your machine, controlled by your OS scheduler
6. **Visibility check is fail-closed** — refuses public targets before any data is sent
