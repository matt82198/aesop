# Installation & Setup

**New repo?** Follow the scaffolding guide below.
**Existing repo?** See [PORTING.md](PORTING.md) for adopting Aesop on a foreign codebase (with 10 common pitfalls covered).

---

## Tier 1: Quick Try (2 min, no API keys)

Explore Aesop without any API keys, Python, or configuration. You only need **Node.js >= 18** and **git**.

```bash
# Clone and install
git clone https://github.com/matt82198/aesop.git
cd aesop
npm install

# See the CLI and all available commands
npx . --help

# Run the preflight readiness check (pure Node.js, no keys needed)
npx . doctor

# Run the full offline test suite
npm test

# Launch the web dashboard (requires Python 3 on PATH)
npx . dash
# Opens http://localhost:8770 — five views: Overview, Work, Activity, Cost
```

Everything above runs locally with zero network calls and no API keys. The `doctor` command checks your environment, the test suite validates the codebase, and the dashboard gives you a feel for the fleet-monitoring UI.

When you are ready to run the actual multi-agent orchestration, continue to Tier 2.

---

## Tier 2: Full Setup (orchestration with LLM agents)

### Prerequisites

To run the orchestration loop (dispatching agents, running waves, shipping code), you need:

- **Claude Code CLI** (v0.1+) — the orchestration harness integration
- **Git** (v2.40+) — version control and worktree support
- **Bash** (v4+) or Git Bash on Windows — shell scripting support
- **Node.js** (v18+) — for dashboard and monitor signals
- **Python** (v3.10+) — for secret-scan, guardrails, and log rotation
- **jq** (optional) — for TUI dashboard parsing

### Install Claude Code CLI

Claude Code is required to run `/power` and `/buildsystem` skills.

**Download and install**:
- Visit https://claude.com/claude-code to download and install Claude Code for your operating system

**Verify installation**:
```bash
claude --version
```

Expected output: `Claude Code vX.Y.Z` (v0.1 or higher)

**If not on PATH**: On macOS/Linux, add the install directory to your `$PATH`. On Windows, the installer registers Claude Code in your PATH automatically. If `claude` is not found:
- Reinstall Claude Code and ensure the installer adds it to PATH
- Or add the installation directory to PATH manually (usually `~/.claude/bin` or `C:\Program Files\Claude Code\bin`)

### Check your versions

```bash
claude --version
git --version
bash --version
node --version
python3 --version
```

---

## Quick Start: npx Scaffold (Recommended)

The fastest way to get started is to use the Aesop template scaffolder. It creates a preconfigured aesop harness in a new directory.

### Step 1: Scaffold the harness

```bash
npx @matt82198/aesop my-fleet \
  --name "my-api" \
  --repos "/path/to/repo1,/path/to/repo2"
```

This creates a `my-fleet/` directory with:
- `daemons/` — watchdog, backup, secret-scan
- `skills/` — /power and /buildsystem skill templates
- `monitor/` — signal collectors
- `ui/` — web dashboard
- `aesop.config.json` — your configuration
- `state/` — runtime checkpoints (git-ignored)
- Pre-installed pre-push hook in `.git/hooks/`

### Step 2: Install orchestrator skills

Copy the skill definitions to your Claude Code home directory:

```bash
cd my-fleet

# Copy /power skill (orchestrator brain)
cp -r skills/power/ ~/.claude/skills/power/

# Copy /buildsystem skill (wave cycle automation)
cp -r skills/buildsystem/ ~/.claude/skills/buildsystem/
```

**Verify the skills were copied**:
```bash
# Check both skills exist
ls -la ~/.claude/skills/power/SKILL.md
ls -la ~/.claude/skills/buildsystem/SKILL.md
```

Expected output: Two SKILL.md files should exist.

### Step 3: Verify the installation

Run the watchdog once to test everything:

```bash
bash daemons/run-watchdog.sh --once
```

Expected output:
```
[watchdog] backing up fleet state...
[watchdog] scanning for secrets...
[watchdog] drift check: (files checked) ✓
[watchdog] all clear
```

If you see errors, check the logs in `state/FLEET-BACKUP.log`.

---

## Zero-Friction Reproduce Path

**For external users or fresh installations**: a single command verifies your Aesop setup end-to-end.

### Single-command verification

```bash
npx @matt82198/aesop reproduce
```

This runs one of two offline verification suites depending on context:

**Repo mode** (if running inside the aesop repository):
- Node syntax check (*.mjs files)
- Shell syntax check (*.sh files)
- Node.js test suites (`npm run test:node`)
- Shell test suites (`npm run test:sh`)
- React component tests (vitest)
- Python tool import/compile smoke tests
- Python unit tests
- Benchmark scorer tests
- Offline benchmark reproduction

**Installed mode** (for scaffolded fleets or npm-installed packages):
- Preflight checks (`aesop doctor`) — verifies Node/Python/git/config/hooks
- Health score check (`aesop health-score`) — verifies daemon and state-store health
- Secret-scan selftest — validates credential detection rules
- Packaging assertions — ensures required directories and files are present

**Output**:
- ASCII table with per-check timing and status (PASS/SKIP/FAIL)
- Summary with pass count and total time
- Exit code 0 if all checks pass, 1 if any fail

**Examples**:

```bash
# Repo: full offline test suite
cd ~/my-aesop && npx . reproduce

# Installed: quick health verification
npx @matt82198/aesop reproduce
```

### Three-command offline sequence (if reproduce fails)

If you want to troubleshoot the reproduce command step-by-step:

```bash
# Step 1: Preflight check (Node, Python, git, config, directories, hooks)
npx @matt82198/aesop doctor

# Step 2: Health check (daemon heartbeats, state-store readiness)
npx @matt82198/aesop health-score

# Step 3: Secret-scan selftest (verify credential detection)
python tools/scanner_selftest.py
```

Each command prints diagnostic output and exits non-zero if anything fails. This sequence is equivalent to the full `reproduce` suite but allows you to inspect failures individually.

---

## Manual Setup: Git Clone (For Development)

If you're hacking on Aesop itself, clone the repo and set up manually:

### Step 1: Clone and configure

```bash
git clone https://github.com/matt82198/aesop ~/my-aesop
cd ~/my-aesop

# Create your configuration
cp aesop.config.example.json aesop.config.json
```

### Step 2: Edit aesop.config.json

Open `aesop.config.json` and customize for your repos (see [CONFIGURE.md](CONFIGURE.md) for full details):

```json
{
  "aesop_root": "/home/user/my-aesop",
  "brain_root": "/home/user/.claude",
  "repos": [
    {
      "path": "/home/user/my-repo1",
      "name": "my-api"
    },
    {
      "path": "/home/user/my-repo2",
      "name": "my-frontend"
    }
  ],
  "dashboard": {
    "port": 8770
  },
  "dashboardOrigin": "http://localhost:8770"
}
```

### Step 3: Install skills and test

```bash
# Copy skills to Claude Code
cp -r skills/power/ ~/.claude/skills/power/
cp -r skills/buildsystem/ ~/.claude/skills/buildsystem/

# Set environment variable (add to ~/.bashrc or ~/.zprofile)
export AESOP_ROOT=/home/user/my-aesop

# Verify
bash $AESOP_ROOT/daemons/run-watchdog.sh --once
```

---

## What Gets Created

After setup, you'll have:

### Main directories

- **daemons/** — Background watchdog (runs every 150s)
  - `run-watchdog.sh` — main daemon loop
  - `backup-fleet.sh` — backs up work to a safe branch
  - `secret-scan.py` — blocks pushes with detected credentials

- **state/** — Runtime checkpoints (git-ignored)
  - `STATE.md` — current phase and NEXT STEPS
  - `BUILDLOG.md` — append-only progress log
  - `.watchdog-heartbeat` — daemon liveness marker

- **skills/** — Claude Code orchestration skills
  - `power/` — /power skill template (prime orchestrator brain)
  - `buildsystem/` — /buildsystem skill template (wave cycle automation)

- **monitor/** — Signal collectors
  - `collect-signals.mjs` — health checks (extensible)

- **ui/** — Web dashboard
  - `serve.py` — Python backend (JSON/SSE APIs)
  - `web/` — React frontend (hash-routed SPA)

- **hooks/** — Git pre-push policies
  - `pre-push-policy.sh` — branch discipline + secret-scan enforcement

- **.git/hooks/pre-push** — Auto-installed pre-push hook (configured during setup)

### Configuration files

- **aesop.config.json** — Main configuration (git-ignored, never commit credentials)
  - `aesop_root` — path to this harness directory
  - `brain_root` — path to Claude Code home (`~/.claude`)
  - `repos` — list of monitored repositories
  - `dashboard.port` — web dashboard port (default: 8770)
  - `dashboardOrigin` — CORS origin validation

- **aesop.config.example.json** — Template with defaults (commit this, use as reference)

---

## Environment Variables

Optional environment variables you can set in your shell:

```bash
# Point to the Aesop harness root (used by daemons and tools)
export AESOP_ROOT=/home/user/my-aesop

# Optional: custom location for Aesop state directory (default: ./state)
export AESOP_STATE_ROOT=/home/user/my-aesop/state

# Optional: custom location for Claude Code home (used by /power skill)
export BRAIN_ROOT=/home/user/.claude

# Optional: custom location for reusable scripts directory
export SCRIPTS_ROOT=/home/user/scripts
```

---

## Already Have Aesop? Quick Setup

If you've already scaffolded Aesop and just need to get running:

1. **Verify your config**:
   ```bash
   node -e "console.log(JSON.parse(require('fs').readFileSync('aesop.config.json')))"
   ```

2. **Verify the setup**:
   ```bash
   bash daemons/run-watchdog.sh --once
   ```

3. **Copy skills** (if not done yet):
   ```bash
   cp -r skills/power/ ~/.claude/skills/power/
   cp -r skills/buildsystem/ ~/.claude/skills/buildsystem/
   ```

4. **Run your first wave**:
   - Open Claude Code
   - Type `/power` to prime your brain
   - Type `/buildsystem` to start a wave (see [FIRST-WAVE.md](FIRST-WAVE.md))

---

## Using Non-Claude Backends

By default, Aesop uses Claude Code (the orchestration harness) as its backend. You can configure it to use other models via the **AgentDriver abstraction**—enabling Ollama, OpenAI-compatible endpoints, OpenRouter, and more.

For the conceptual picture (the two swappable seats, what's proven vs. bounded about swapping them, and a 60-second copy-paste quickstart), see **[MICROKERNEL.md](MICROKERNEL.md)**. The rest of this section is the config-mechanics reference.

### The unified two-seat config (0.4.0)

One namespaced `seats` block in `aesop.config.json` selects BOTH seats:

- **`seats.worker`** — the coding agents (AgentDriver). Same fields as the
  legacy flat block below; takes precedence over it when both are present.
- **`seats.orchestrator`** — the decision seat (`OrchestratorDriver.decide()`).
  `"harness"` (default) means the live Claude Code session itself makes
  decisions; `"openai-compatible"` routes decisions to an API model.

**Swap a seat's model** — change just its block:

```json
{
  "seats": {
    "worker": {
      "backend": "openai-compatible",
      "base_url": "http://localhost:11434/v1",
      "model": "mistral",
      "is_local": true
    },
    "orchestrator": {
      "backend": "openai-compatible",
      "model": "gpt-4o-mini",
      "api_key_env": "OPENAI_API_KEY"
    }
  }
}
```

That is a local-Ollama worker fleet with a hosted `gpt-4o-mini` decision seat.
To swap the worker to OpenRouter, replace `seats.worker` with
`{"backend": "openai-compatible", "base_url": "https://openrouter.ai/api/v1",
"model": "openai/gpt-4-turbo", "api_key_env": "OPENROUTER_API_KEY"}` — nothing
else changes. API keys are read from the env var named by `api_key_env` at
call time and are never stored in the config; `"is_local": true` endpoints
need no key at all — and for exactly that reason `is_local` is only accepted
with a loopback `base_url` (`localhost`, `127.0.0.1`, `::1`).

`api_key_env` is checked with a **best-effort heuristic**, allowlist-primary:
known LLM-provider key names (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`OPENROUTER_API_KEY`, `TOGETHER_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`,
`DEEPSEEK_API_KEY`, `FIREWORKS_API_KEY`, `OLLAMA_API_KEY`,
`AZURE_OPENAI_API_KEY`, `GOOGLE_API_KEY`) are accepted silently; names that
don't look like key env vars or contain obvious non-LLM secret fragments
(`SECRET`/`TOKEN`/`PASSWORD`/`ACCESS`/...) are rejected; any other
key-shaped name (custom LLM gateways) is **allowed but prints a loud
NOTICE** — the env var's value will be sent as a Bearer token to your
`base_url`, so that NOTICE is the real signal: review it whenever a
non-provider name shows up at load time.

Asymmetry worth knowing: a **worker** `openai-compatible` seat REQUIRES
`base_url`; the **orchestrator** seat may omit it (defaults to the hosted
OpenAI endpoint).

**No `seats` block? Nothing changes.** Existing installs keep today's exact
behavior: Claude Code workers + harness orchestrator, no OpenAI backend
constructed, no key required. A pre-0.4.0 **legacy flat** backend block
(`{"backend": "codex", ...}` at the top level) also changes nothing by
itself: it still parses and validates, but it stays **inert** in the wave
scheduler's default path until you migrate it to `seats.worker` — on older
installs that block was documented but consumed by nothing, so activating it
silently would change behavior under you. (`cardinal_rules.orchestrator_model`
from older scaffolds was write-only and is retired; the orchestrator seat's
model now lives in `seats.orchestrator.model`.)

Consumers: `driver/wave_scheduler.py` builds its worker driver from this
config (CLI `--driver claude|codex` remains an override), and
`tools/shadow_adjudication.py` / `tools/seated_shadow_adjudication.py` build
their live orchestrator backend from `seats.orchestrator` (CLI `--model`
remains an override).

### The legacy flat block (parse-compatible, but migrate it)

Pre-0.4.0 docs described a flat top-level block:

```json
{
  "backend": "openai-compatible",
  "model": "ollama-mistral",
  "base_url": "http://localhost:11434/v1",
  "api_key_env": "OLLAMA_API_KEY",
  "is_local": true
}
```

It still parses and validates (and direct `build_driver()` callers honor
it), but the wave scheduler's default path treats it as **inert** and keeps
the Claude Code worker: to actually activate a configured worker there, put
the same fields under `seats.worker` (`{"seats": {"worker": { ...this
block... }}}`).

Set `"is_local": true` for local/small models (Ollama etc.) — it raises the
verification tier honestly (tier 3 instead of hosted tier 2).

Supported backends:
- `"claude"` (default) — Claude Code CLI harness
- `"openai-compatible"` — OpenAI Chat Completions API (Ollama, OpenRouter, etc.)
- `"codex"` — CodeX OpenAI backend (legacy)

### Example: Local Ollama

To run Aesop against Mistral locally via Ollama:

```bash
# 1. Install Ollama (https://ollama.ai) and start the daemon
ollama serve

# 2. In another terminal, pull a model
ollama pull mistral

# 3. Configure Aesop to use it (seats.worker is the opt-in surface)
cat > aesop.config.json <<EOF
{
  "seats": {
    "worker": {
      "backend": "openai-compatible",
      "model": "mistral",
      "base_url": "http://localhost:11434/v1",
      "api_key_env": "OLLAMA_API_KEY",
      "is_local": true
    }
  }
}
EOF

# 4. Start Aesop (components that dispatch through the AgentDriver seam will use Mistral)
npx @matt82198/aesop my-fleet --name "my-api"
```

### Verification tiers: weaker backends get more checking

The AgentDriver framework applies **honest verification tiers** — weaker backends (lower accuracy, no structured output) trigger stronger verification in the orchestrator:

| Backend | Accuracy | Verification Tier | What it means |
|---------|----------|-------------------|---------------|
| Claude Code | ~0.99 | 1 (minimal) | Orchestrator trusts output; spot-check tests |
| Hosted OpenAI-compatible (codex / OpenRouter) | ~0.92 | 2 | Validate all JSON, ~50% spot-check, adversarial review |
| Local small model (`"is_local": true`, e.g. Ollama) | ~0.80 | 3 | Validate all JSON, heavy spot-check, adversarial review |

Higher tiers mean MORE orchestrator verification work: weaker backends raise, never lower, the orchestrator's burden. See [driver/README.md](../driver/README.md) for full verification-policy details.

### Troubleshooting

**Backend won't connect**: Check `OLLAMA_API_KEY` (or your backend's API key env var) is set and the `base_url` is reachable.

**Verification tier too strict**: The tier comes from the driver's `probe_capabilities()` honesty contract — never inflate accuracy to lower it. For hosted-quality models, leave `"is_local"` unset (tier 2); reserve `"is_local": true` (tier 3) for genuinely small local models.

For more details, see [driver/README.md](../driver/README.md).

---

## Pre-push Hook Installation

The `npx` scaffold installs the pre-push hook automatically. If you cloned the repo manually, install it:

```bash
mkdir -p .git/hooks
cp hooks/pre-push-policy.sh .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

The hook enforces:
- Feature branches only (never direct pushes to `main`/`master`)
- Secret scanning (blocks commits with detected credentials)

To bypass during testing: `git push --no-verify` (not recommended for production).

---

## Windows: Register Daemons as Hidden Scheduled Tasks

On Windows, the watchdog and refinement monitor daemons can run silently in the background without flashing a console window. Use the provided PowerShell installer:

```powershell
# Register watchdog daemon (every 5m)
powershell -NoProfile -ExecutionPolicy Bypass -File daemons/install-tasks.ps1

# Register both watchdog and monitor daemons (monitor script is external, customize path as needed)
powershell -NoProfile -ExecutionPolicy Bypass -File daemons/install-tasks.ps1 `
  -MonitorCommand "bash '/c/path/to/your/monitor/run-monitor.sh' --once"

# Customize intervals and task names
powershell -NoProfile -ExecutionPolicy Bypass -File daemons/install-tasks.ps1 `
  -TaskPrefix MyFleet `
  -WatchdogIntervalMinutes 10 `
  -MonitorIntervalMinutes 30 `
  -MonitorCommand "bash '/c/path/to/your/monitor/run-monitor.sh' --once"

# Uninstall tasks
powershell -NoProfile -ExecutionPolicy Bypass -File daemons/install-tasks.ps1 -Uninstall

# Preview without registering (dry-run mode)
powershell -NoProfile -ExecutionPolicy Bypass -File daemons/install-tasks.ps1 -DryRun
```

**How it works**: The installer creates Scheduled Tasks that launch `wscript.exe` with a hidden VBScript launcher (`daemons/run-hidden.vbs`). This avoids the console window that appears when bash.exe is run directly as a Scheduled Task action.

**Parameters**:
- `-TaskPrefix AesopMyFleet` — Task names: `AesopMyFleetWatchdogDaemon`, `AesopMyFleetRefinementMonitor` (default: `Aesop`)
- `-WatchdogIntervalMinutes N` — Watchdog cycle interval in minutes (default: 5)
- `-MonitorIntervalMinutes N` — Monitor cycle interval in minutes (default: 20)
- `-WatchdogCommand "bash '...' ..."` — Custom watchdog command (default: `run-watchdog.sh --once >> state/cron-watchdog.log`)
- `-MonitorCommand "bash '...' ..."` — Custom monitor command; omit to skip registering the monitor task (default: empty)
- `-Uninstall` — Remove all registered tasks
- `-DryRun` — Preview task configuration without registering

**Constraints**:
- Commands (`-WatchdogCommand`, `-MonitorCommand`) must NOT contain double quotes (vbs launcher contract)
- UNC paths (e.g., `\\server\share`) are not supported; use local Windows or POSIX paths only
- `-DryRun` mode works even if `bash.exe` or run-hidden.vbs is missing (validation downgraded to warnings for preview)

---

## Next Steps

1. **Read [PORTING.md](PORTING.md)** — Step-by-step guide for adopting Aesop on a foreign repo (10 common failure modes)
2. **Read [CONFIGURE.md](CONFIGURE.md)** — Customize repos, ports, and brain root
3. **Run [FIRST-WAVE.md](FIRST-WAVE.md)** — Test a full `/power` → `/buildsystem` cycle
4. **Understand [CONCEPTS.md](CONCEPTS.md)** — Learn the dispatch model and state model
5. **Read [MICROKERNEL.md](MICROKERNEL.md)** — Swap the worker or orchestrator seat to a non-Claude model
6. **Explore the dashboard** — `python3 ui/serve.py` then open http://localhost:8770

For troubleshooting, see the [Aesop README](../README.md#troubleshooting) or [GOVERNANCE.md](GOVERNANCE.md) for operational policies.
