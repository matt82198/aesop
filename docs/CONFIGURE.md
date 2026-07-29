# Configuration

**TL;DR**: Edit `aesop.config.json` to tell Aesop which repos to watch, where your Claude Code brain lives, and which ports to use.

---

## aesop.config.json

After setup, you'll have an `aesop.config.json` in your harness root. This file tells Aesop about your environment.

```json
{
  "aesop_root": "/home/user/aesop",
  "brain_root": "/home/user/.claude",
  "repos": [
    {
      "path": "/home/user/my-api",
      "name": "api-backend"
    },
    {
      "path": "/home/user/my-frontend",
      "name": "web-frontend"
    }
  ],
  "dashboard": {
    "port": 8770
  },
  "dashboardOrigin": "http://localhost:8770"
}
```

### Field Reference

#### `aesop_root` (string, required)

Absolute path to this Aesop harness directory. Used by daemons to locate scripts and state files.

**Example**: `/home/user/aesop` or `C:\Users\user\aesop` (Windows)

**Default**: Set automatically during `npx` scaffold.

---

#### `brain_root` (string, required)

Absolute path to your Claude Code home directory. This is where skills (`.claude/skills/`) and memory (`.claude/MEMORY.md`) live.

**Example**: `/home/user/.claude`

**Typical value**: `~/.claude` expanded to your home directory.

**Why it matters**: The `/power` skill primes the orchestrator brain from this root. Aesop loads CLAUDE.md rules, MEMORY.md facts, and skills from here.

---

#### `repos` (array of objects, required)

List of repositories Aesop should watch and monitor.

Each repo object has:

- **`path`** (string): Absolute path to the repository root
- **`name`** (string): Short human-readable name (used in logs, dashboards, status)

**Example**:
```json
"repos": [
  {
    "path": "/home/user/tr-sample-tracker",
    "name": "tracker"
  },
  {
    "path": "/home/user/my-api",
    "name": "api"
  }
]
```

**Why multiple repos?** Aesop can orchestrate work across your entire codebase — microservices, monorepos, or a suite of related projects. Each repo is monitored independently, but the orchestrator sees all of them.

---

#### `dashboard.port` (number, default: 8770)

HTTP port for the web dashboard. The dashboard runs a Python HTTP server on this port.

**Example**: `8770`, `9000`, or any free port.

**To verify the port is free**:
```bash
# On macOS/Linux
lsof -i :8770

# On Windows (PowerShell)
Get-NetTCPConnection -LocalPort 8770
```

If the port is in use, pick a different one or kill the process using it.

---

#### `dashboardOrigin` (string, default: http://localhost:8770)

The origin (scheme + host + port) for the dashboard. Used for CORS validation and CSRF protection.

**Example**:
- Local development: `http://localhost:8770`
- Remote machine: `http://192.168.1.100:8770`
- Behind a proxy: `https://aesop.example.com`

**Why it matters**: The dashboard validates incoming requests against this origin to prevent HTTP header injection attacks. If your CORS origin changes (e.g., you expose the dashboard publicly), update this.

---

## Example Configurations

### Single repo, local development

```json
{
  "aesop_root": "/home/user/aesop",
  "brain_root": "/home/user/.claude",
  "repos": [
    {
      "path": "/home/user/my-app",
      "name": "my-app"
    }
  ],
  "dashboard": {
    "port": 8770
  },
  "dashboardOrigin": "http://localhost:8770"
}
```

### Microservices architecture

```json
{
  "aesop_root": "/home/user/aesop",
  "brain_root": "/home/user/.claude",
  "repos": [
    {
      "path": "/home/user/services/auth",
      "name": "auth-service"
    },
    {
      "path": "/home/user/services/api",
      "name": "api-gateway"
    },
    {
      "path": "/home/user/services/data",
      "name": "data-service"
    },
    {
      "path": "/home/user/infra",
      "name": "infrastructure"
    }
  ],
  "dashboard": {
    "port": 8770
  },
  "dashboardOrigin": "http://localhost:8770"
}
```

### Windows paths

```json
{
  "aesop_root": "C:\\Users\\user\\aesop",
  "brain_root": "C:\\Users\\user\\.claude",
  "repos": [
    {
      "path": "C:\\Users\\user\\projects\\my-api",
      "name": "api"
    }
  ],
  "dashboard": {
    "port": 8770
  },
  "dashboardOrigin": "http://localhost:8770"
}
```

---

## Security Notes

### git-ignore aesop.config.json

**Never commit `aesop.config.json` to git.** It may contain local paths, private repo paths, or other local configuration.

Add to `.gitignore`:
```
aesop.config.json
```

Commit `aesop.config.example.json` instead, with safe default values.

### Keep secrets out of the config

Aesop credentials (GitHub tokens, API keys) should never go in `aesop.config.json`. Instead:

1. Set them as environment variables
2. Use GitHub Actions secrets for CI/CD
3. Store them in your `~/.claude/` private brain (which is git-ignored)

---

## Environment Variables

You can override config values via environment variables (optional):

```bash
# Override the Aesop root (used by daemons)
export AESOP_ROOT=/path/to/aesop

# Override the Aesop state directory (default: ./state)
export AESOP_STATE_ROOT=/path/to/state

# Override the Claude Code home (used by /power skill and orchestrator)
export BRAIN_ROOT=/path/to/.claude

# Override the scripts directory
export SCRIPTS_ROOT=/path/to/scripts
```

If set, these override the values in `aesop.config.json`.

---

## Validating Your Configuration

After editing `aesop.config.json`, validate it:

```bash
# Check JSON syntax
node -e "console.log(JSON.parse(require('fs').readFileSync('aesop.config.json')))"

# Run the watchdog once to verify setup
bash daemons/run-watchdog.sh --once
```

Expected output:
```
[watchdog] backing up fleet state...
[watchdog] scanning for secrets...
[watchdog] drift check: (files checked) ✓
[watchdog] all clear
```

If you see errors, check:
- All paths exist and are accessible
- JSON syntax is valid (no trailing commas)
- Git is initialized in all listed repos

---

## Next Steps

1. **Read [FIRST-WAVE.md](FIRST-WAVE.md)** — Run your first `/power` → `/buildsystem` cycle
2. **Review [CONCEPTS.md](CONCEPTS.md)** — Understand the dispatch model
3. **Launch the dashboard** — `python ui/serve.py` (verify it loads)
4. **Join the orchestration** — Type `/power` in Claude Code to prime your brain, then `/buildsystem` to start a wave

For operational governance (heartbeat protocol, single-writer files, etc.), see [GOVERNANCE.md](GOVERNANCE.md).
