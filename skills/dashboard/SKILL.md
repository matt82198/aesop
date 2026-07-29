---
name: dashboard
description: Launch the aesop web dashboard — idempotent health check, background serve, browser open, and optional stop.
version: 1.0.0
---

# /dashboard — Launch the aesop web dashboard

Idempotent dashboard launch: check if already running on :8770, start if needed (background process),
open browser, report URL + task id. Optional `stop` argument to kill the server.

**Model**: Haiku (read-only + light operations)

## Behavior

### 1. Check dashboard health (no startup on this call)
```bash
curl -s -o /dev/null -w '%{http_code}' http://localhost:8770/
```
- If **200**: dashboard already running → report URL + "Already running" + skip to step 4.
- If **non-200 or connection refused**: proceed to step 2.

### 2. Start the dashboard server (background)
From the aesop repo root, launch via Bash `run_in_background: true`:
```bash
python ui/serve.py
```

Environment override (optional):
- `PORT=<port>` — defaults to 8770 if unset.
- Config precedence: `PORT` env var > `aesop.config.json` > default 8770

### 3. Poll for readiness (no blocking sleeps)
Use `curl --retry` to poll until :8770 responds with 200:
```bash
curl -s --retry 20 --retry-delay 1 --retry-connrefused http://localhost:8770/
```

Expect **200** — then proceed.

### 4. Open browser (Windows)
```powershell
start http://localhost:8770
```

### 5. Report
Output one summary line with:
- Dashboard URL: `http://localhost:8770`
- Status: "Running" or "Already running"
- Background task id (if just started)
- Data freshness: hit `/api/state` for one-line status (e.g., "heartbeat: 5s old")

**Example:**
```
Dashboard: http://localhost:8770 | Running (task: bg-1234) | heartbeat: 5s old
```

---

## Optional: `stop` argument

If invoked with argument `stop`:
```bash
# Find the port-holder PID
netstat -ano | findstr :8770

# Kill by PID
taskkill /PID <pid> /F

# Verify port freed (expect connection refused / non-200)
curl -s -o /dev/null -w '%{http_code}' http://localhost:8770/
```

Report: "Dashboard stopped" + port verification.

---

## Implementation notes

- Idempotent: repeat calls when server is up → no-op start, just open.
- No blocking sleeps: `curl --retry-delay 1` gives server time without busy-wait.
- ThreadingHTTPServer required: the Python backend uses one thread per SSE client (Server-Sent Events).
- Token file: `state/.ui-session-token` (regenerated if missing, 0600).
- Config: PORT from env or `aesop.config.json` (query `ui/config.py` for precedence).
- Repo-relative: `python ui/serve.py` assumes cwd is the aesop repo root.

---

## Related

- **serve.py** location: `ui/serve.py` (repo-relative)
- **Config precedence**: env PORT > aesop.config.json > default 8770
- **API routes**: `/api/state`, `/api/wave/prs`, `/api/cost`, `/events` (SSE).
- **Dashboard docs**: `ui/CLAUDE.md`
