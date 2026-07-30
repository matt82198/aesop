# bin/ — CLI scaffolder + runtime commands

**Domain**: Node.js CLI entry point (`bin/cli.js`): scaffolds aesop orchestration template, interactive onboarding wizard, runtime subcommand dispatch (doctor/watch/dash/status/fleet/health-score/reproduce/init).

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only; dummy secrets are runtime-concatenated, never literal.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## Invocation modes

**Scaffolder** (creates new fleet):
- `npx @matt82198/aesop` → scaffolds to `./aesop-fleet` (default)
- `npx @matt82198/aesop my-fleet` → scaffolds to `./my-fleet`
- `npx @matt82198/aesop my-fleet --force` → re-scaffold and replace `.git/hooks/pre-push`
- `npx @matt82198/aesop --name "api" --domains "server,worker" --repos "/path/repo"` → headless scaffold (no interactive prompts)

**Wizard** (guided setup, ~60 sec):
- `npx @matt82198/aesop wizard` → prompts: project name (default: "my-fleet"), repos (auto-discover from `~`), port (default: 8770, validates 1–65535), brain root (default: `~/.claude`)
- `npx @matt82198/aesop wizard --yes` → uses all defaults, no prompts (CI-safe)
- Non-TTY input auto-skips prompts

**Runtime commands** (after scaffolding; dispatch pattern in cli.js):
```javascript
const runtimeCommands = ['doctor', 'watch', 'dash', 'status', 'fleet', 'health', 'health-score', 'reproduce', 'init'];
// 'init' dispatches to Python: spawnSync('python3', ['tools/init_project.py', '--dir', '.', ...])
// All others load+run a Node module:
const commandMap = {
  'doctor': '../tools/doctor.js',   // Preflight check (Node, Python, git, config, dirs, hooks, port)
  'watch': '../tools/watch.js',     // Launch daemon; spawns daemons/run-watchdog.sh
  'dash': '../tools/dash.js',       // Launch web dashboard; spawns python3 ui/serve.py (default localhost:8770)
  'status': '../tools/status.js',   // One-shot snapshot (heartbeats, port, git branch)
  'fleet': '../tools/fleet.js',     // One-shot JSON snapshot (agents, heartbeats, tracker, orchestrator; Node STDLIB only, graceful degrade)
  'health': '../tools/health.js'    // Fleet health aggregator (heartbeats, alerts, orchestrator, tracker; Python wrapper)
};
require(commandMap[args[0]]); // Load + run; returns immediately after
```

**Init command** (`npx @matt82198/aesop init`):
- Scaffolds aesop orchestration into the CURRENT directory (not a new fleet dir)
- Creates: CLAUDE.md, domain CLAUDE.md for first code dir, aesop.config.json, state/.gitkeep, .github/workflows/ci.yml, pre-push hook
- Flags: `--name <name>` (project name, auto-detected from git remote), `--force` (overwrite existing files)
- Delegates to `tools/init_project.py` via spawnSync (python3 with python fallback)

## Scaffold files (filesToCopy array in cli.js lines 243–260)

**Directories copied** (recursive):
`daemons/`, `dash/`, `monitor/`, `tools/`, `ui/`, `docs/`, `state_store/`, `skills/`, `mcp/`, `scan/`, `hooks/`, `driver/`

**Files copied**:
`aesop.config.example.json`, `README.md`, `LICENSE`, `CHANGELOG.md`, `CLAUDE-TEMPLATE.md`

**NOT copied**:
`aesop.config.json` (user must `cp aesop.config.example.json` and edit), `state/` (runtime durable state, git-ignored), `.git/`, `node_modules/`, build artifacts

**npm package.json `files` array** (lines 9–36): If adding new dirs/files to `filesToCopy`, add to `files` array so npm publish includes them.

## Invariants

- **Idempotent on empty targets**: Fails if `targetDir` exists and is non-empty (except `.git`, aesop scaffolded dirs). Safe to retry.
- **Symlink guard**: Rejects symlinks in target dir (lstat check, not stat); prevents escaping targetDir during copy.
- **Portable paths**: No machine-specific paths; `path.join()` + `__dirname` handle cross-platform resolution. Config uses `~` form (`~/.claude`, `~/scripts`) expanded at load time.
- **Async wizard**: Main execution is async IIFE to support readline prompts.
- **Non-destructive**: Never overwrites existing `aesop.config.json` without user confirmation.

## Test commands

**Node.js tests** (npm run test:node):
- `npm run test:node` → `node --test --test-timeout=60000 tests/*.test.mjs`
- Fleet CLI tests: `tests/fleet-cli.test.mjs` — spawns CLI in temp fixture, verifies JSON shape (heartbeats, agents, tracker, orchestrator), graceful degrade, exit 0, no cwd pollution
- CLI config tests: `tests/cli-config.test.mjs` — scaffold flags (--name, --domains, --repos, --repo-urls), fleet_root auto-set to os.homedir(), config validation, repo URL generation

**First-hour test suite** (inline in both test files above):
- Empty state directory graceful degrade (no state files)
- Present heartbeat files parsed correctly
- Invalid/malformed JSON degrades gracefully
- Process timeouts handled (10s max per invocation)

**Shell integration** (npm run test:sh): Pre-push hook tests + watchdog tests (separate domains, see hooks/ and daemons/)

## Dropped (reason)
- Post-scaffold user guidance steps (belongs in README/CLAUDE-TEMPLATE.md output, not scaffolder contract)
- Repo discovery implementation details (discoverRepos function is simple, documented inline in code)
- Port validation logic (validatePort function documented inline; accepts 1–65535)
- Flag parsing implementation (getFlag function is simple; documented inline)

Map of all domains: /CLAUDE.md
