# dash/ — Real-time TUI fleet monitoring (watchdog + agent activity)

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only; dummy secrets are runtime-concatenated, never literal.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## Files & responsibilities

- **watchdog-gui.sh** — TUI dashboard: 4s refresh loop, double-buffered (no flicker), displays daemon status/heartbeats/alerts. Read-only. Launch in dedicated terminal window (never inside tool shell). CRLF-safe (no line continuations), POSIX portable.
- **dash-extra.mjs** — Agent activity detector: scans transcript JSONLs (modified <12min), reads security alerts. Two output modes: TUI (default) or JSON (`--json` flag for web/CLI consumers). Gracefully prints "unavailable" if node missing (doesn't fail watchdog).

## State contracts

**watchdog-gui.sh reads** (configured via `AESOP_ROOT`, default: `.`; all paths relative to it):
- `$CONDUCTOR_ROOT/state/.watchdog-heartbeat` — watchdog heartbeat (epoch seconds), the path backup-fleet.sh writes; resolved by `resolve_conductor_root` in `daemons/conductor-root.sh` (`CONDUCTOR_ROOT` env wins; else the sibling of the canonical `aesop` tree if it holds `state/.watchdog-heartbeat`; else `$HOME/conductor3` under the same heartbeat test; else no path → the GUI prints `UNRESOLVED`, never STALE or ALIVE). A bare `state/` dir is not a marker: a stray sibling can hold only `state/pr-sweep.log`. watchdog-gui.sh sources that helper and never re-derives the path itself (keeps the portability ratchet clean). Never `AESOP_ROOT/state`.
- `state/FLEET-BACKUP.log` — append-only recent events (tail -3 displayed)
- `state/SECURITY-ALERTS.log` — all lines; counts HIGH/MED (filters RESOLVED-FP); displayed as badge
- `state/.watchdog-repos.json` — JSON array of repo status (jq-parsed)
- `state/.heartbeats/*` — per-agent epoch files; per-agent thresholds from config (see below)

Heartbeat thresholds loaded from `aesop.config.json` (if present + jq available; else built-in defaults):
- `.monitor.heartbeat_thresholds.monitor` (default 3600s)
- `.monitor.heartbeat_thresholds.watchdog` (default 300s)
- `.monitor.heartbeat_thresholds.default` (default 1800s)

**dash-extra.mjs reads** (env var > config > default; all paths):
- `AESOP_ROOT` (env) or `<home>/aesop` → `aesop.config.json` → expanded via tilde + `$VAR` substitution
- Home is `os.homedir()` (USERPROFILE on Windows, HOME on POSIX), never `process.env.HOME` alone: HOME is unset on native Windows PowerShell and scheduled-task launches.
- `AESOP_TRANSCRIPTS_ROOT` (env) or config `.transcripts_root` or `<home>/.claude/projects` → scans `agent-*.jsonl` files (mtime <12min, top 8 recent)
- `AESOP_STATE_ROOT` (env) or config `.state_root` or `{AESOP_ROOT}/state` → reads `SECURITY-ALERTS.log` for severity (HIGH/MED/DRIFT)
- Local cache: `{STATE_ROOT}/.dash-extra-cache.json` (persisted metadata; optimization only, safe to drop)

## Invariants & gotchas

- **Read-only dashboards** — no state mutations; both tools are observers only.
- **Infinite loop design** — watchdog-gui.sh runs forever; always launch in dedicated terminal, never inside a tool shell (will block the shell indefinitely).
- **CRLF-safe** — watchdog-gui.sh uses no bash line continuations; portable across CRLF/LF line endings.
- **Node optional** — dash-extra.mjs gracefully degrades if node missing; watchdog does not fail (prints "unavailable" for agents panel).
- **Double-buffered rendering** — watchdog-gui.sh uses ANSI codes (`\033[H`, `\033[J`) to avoid flicker; cursor hidden/restored.

## Fleet CLI consumer contract (dash-extra.mjs JSON mode)

**Input**: `node dash-extra.mjs --json`
**Output**: JSON array of agent objects with fields:
```
{
  "name": "agent-<timestamp>",
  "path": "full/path/to/agent-<timestamp>.jsonl",
  "mtime": <unix_ms>,
  "age": "<display_string>",
  "dispatch": "<prompt_summary>",
  "tokens": { "input": <n>, "output": <n>, "total": <n> },
  "task": "<label>",
  "runtime": "<duration>"
}
```
Callers must handle parse errors gracefully (malformed JSONL lines skipped, cache corruption tolerated).

## Test/launch commands

**Single run** (verify output):
```bash
AESOP_ROOT=. bash dash/watchdog-gui.sh &
sleep 5 && kill %1  # kill after 5s to break infinite loop
```

**Standalone TUI** (monitor dashboard):
```bash
AESOP_ROOT=/path/to/aesop bash dash/watchdog-gui.sh
# Ctrl-C to exit
```

**Agent activity (TUI)**:
```bash
node dash/dash-extra.mjs
```

**Agent activity (JSON for web/CLI)**:
```bash
AESOP_TRANSCRIPTS_ROOT=~/.claude/projects node dash/dash-extra.mjs --json | jq '.[] | select(.tokens.total > 1000)'
```

## Dropped (reason)
- Detailed render_frame() implementation — moved to watchdog-gui.sh code comments.
- Config loading boilerplate for dash-extra.mjs — inlined as env var precedence rule above.
- ANSI color definitions (R/G/Y/M/C/B/D/X) — implementation detail, not needed for workers.
