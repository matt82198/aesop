# GitHub Actions Dispatch Workflow Template

## Overview

The **Aesop Dispatch** workflow template provides a ready-to-fork GitHub Actions workflow for integrating aesop multi-agent orchestration into your CI/CD pipeline.

Use this template to:
- Run preflight health checks on a schedule or manually
- Trigger wave orchestration from the GitHub UI
- Post cost summaries and failure visibility reports
- Customize orchestration behavior via environment variables

## Quick Start

### 1. Copy the template to your repository

```bash
cp templates/aesop-dispatch-template.yml .github/workflows/aesop-dispatch.yml
```

### 2. Customize environment variables

Edit `.github/workflows/aesop-dispatch.yml` and update the `env` section:

```yaml
env:
  AESOP_COST_CEILING: '50'          # Token cost limit ($ USD)
  AESOP_CONCURRENCY: '4'             # Max parallel agents
  # AESOP_ORCHESTRATOR_MODEL: 'haiku' # Uncomment to override
```

### 3. Enable manual trigger

- Go to **Actions** → **Aesop Dispatch** in your repo
- Click **Run workflow** (upper right)
- Select an operation from the dropdown:
  - `doctor` — Preflight readiness check
  - `status` — One-shot fleet status snapshot
  - `fleet` — JSON fleet snapshot (agents, heartbeats, tracker)
  - `reproduce` — Offline verification suite
  - `wave preflight` — Wave readiness validator
  - `wave scorecard` — Wave quality scorecard

### 4. (Optional) Schedule a recurring check

Uncomment the `schedule` section in the workflow file:

```yaml
schedule:
  - cron: '0 9 * * 1'  # Every Monday at 9am UTC
```

[Cron syntax reference](https://crontab.guru/)

## Workflow Steps

### Checkout
Clones the repository with full history (required for orchestration tools).

### Setup Node.js
Installs Node.js 20 LTS and caches npm dependencies.

### Setup Python
Installs Python 3.11 and caches pip dependencies (required for orchestration tools like state_store, tracker, cost analysis).

### Orchestration
Runs the selected operation via `npx aesop@latest`. All invoked commands are verified to exist in the aesop CLI.

**Supported commands:**
- `aesop doctor` — Preflight check (config, hooks, CLAUDE.md, state, heartbeats, git identity, secret-scan)
- `aesop status` — One-shot snapshot (heartbeats, port, git branch)
- `aesop fleet` — JSON snapshot (agents, heartbeats, tracker, orchestrator)
- `aesop reproduce` — Offline verification suite (full repo tests or installed checks)
- `aesop wave preflight` — Wave readiness validator
- `aesop wave scorecard` — Wave quality scorecard

**Customize the command:**
Edit the Orchestration step's `run:` field to invoke a different command or pass additional flags:
```bash
npx aesop@latest wave preflight --json
npx aesop@latest status --json
```

### Cost Summary (Pull Requests)
Posts a comment to the PR with token usage and cost estimates.

**⚠️ Important:** PRs created or updated via GITHUB_TOKEN in GitHub Actions do NOT trigger subsequent workflow runs (by GitHub design, to prevent infinite loops). Document this when automating cost tracking.

**Customize:** Edit the script to parse your actual cost ledger (e.g., `state/fleet-ledger.jsonl`) and format the output.

### Failure Visibility
Creates or updates a GitHub issue on failure, surfacing orchestration errors for triage.

**Customize:** Edit the issue title, labels, and body per your project's triage process.

## Permissions

The workflow requires the following permissions (configured in the template):

```yaml
permissions:
  issues: write           # Create/update failure issues
  pull-requests: write    # Post cost-summary comments
  contents: read          # Read repository content
```

## Deployment Secrets & Configuration

### Environment Variables

- **`AESOP_COST_CEILING`** (recommended: `'50'`) — Token cost limit in USD. Orchestration halts if exceeded.
- **`AESOP_CONCURRENCY`** (recommended: `'4'`) — Maximum number of parallel agents.
- **`AESOP_ORCHESTRATOR_MODEL`** (optional: `'haiku'`, `'sonnet'`, or `'opus'`) — Override the orchestrator seat model.

### API Keys

If your orchestration tools require API keys (e.g., `ANTHROPIC_API_KEY`), store them as **GitHub Secrets** and reference them in the workflow:

```yaml
- name: Orchestration
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
  run: npx aesop@latest doctor
```

## Examples

### Example 1: Daily Health Check

```yaml
schedule:
  - cron: '0 9 * * *'  # Every day at 9am UTC

jobs:
  orchestration:
    runs-on: ubuntu-latest
    steps:
      # ... (checkout, setup steps)
      - name: Orchestration
        run: npx aesop@latest doctor
```

### Example 2: Wave Scorecard with Cost Summary

```yaml
- name: Orchestration
  id: orchestration
  run: npx aesop@latest wave scorecard --json > wave-score.json

- name: Cost Summary
  if: success()
  run: cat wave-score.json | jq .
```

### Example 3: Manual Trigger with Custom Config

```yaml
- name: Orchestration
  run: |
    export AESOP_CONCURRENCY=8
    export AESOP_COST_CEILING=100
    npx aesop@latest wave preflight
```

## Troubleshooting

### "npx: command not found"
Verify the **Setup Node.js** step completes successfully. Check the workflow log for errors.

### "python: command not found" or "module not found"
Verify the **Setup Python** step completes successfully. Orchestration tools require Python 3.11+ and standard library modules (no external dependencies).

### Workflow does not trigger on schedule
Check the cron expression at [crontab.guru](https://crontab.guru/). GitHub Actions uses UTC timezone.

### Cost summary comment does not appear on PRs
- Verify permissions include `pull-requests: write`
- Ensure the Orchestration step succeeds
- Customize the `Cost Summary` step's script to parse your actual cost ledger format
- Remember: PRs created/updated via GITHUB_TOKEN do NOT re-trigger CI

### GITHUB_TOKEN caveat
GitHub Actions uses GITHUB_TOKEN for authentication to prevent infinite workflow loops. Any commits, pushes, or PR updates from the workflow will NOT trigger subsequent runs on that push. This is intentional and documented in the template comments.

## FAQ

**Q: Can I run multiple operations in parallel?**
A: The template runs a single operation per workflow run. To parallelize, create multiple jobs or refactor the Orchestration step.

**Q: Can I pass custom flags to aesop commands?**
A: Yes. Edit the Orchestration step's `run:` field:
```bash
npx aesop@latest doctor --json
npx aesop@latest wave preflight --root /path/to/repo
```

**Q: How do I integrate with my existing CI/CD pipeline?**
A: Use the template as-is for standalone orchestration, or copy the Orchestration step into your existing workflow.

**Q: Can I use this template in a private repository?**
A: Yes. The template uses only standard npm/GitHub Actions and does not require external services (except aesop's npm package).

## See Also

- [Aesop README](../README.md) — Full orchestration guide
- [aesop CLI reference](../bin/CLAUDE.md) — CLI commands and options
- [GitHub Actions documentation](https://docs.github.com/en/actions)
