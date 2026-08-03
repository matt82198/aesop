# tools/ — Build utilities

Local-only Python (stdlib only, no external deps), bash (POSIX, CRLF-safe).

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only; dummy secrets are runtime-concatenated, never literal.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## Core invariants

- **Never print secrets**: mask as pattern name + masked value only; NEVER output raw credentials/tokens.
- **AESOP_STATE_ROOT**: all heartbeat/ledger/logs use `AESOP_STATE_ROOT` env var (default `./state`) or CLI args; no hardcoded personal paths.
- **Fragment-assembled secrets in tests**: `scanner_selftest.py` concatenates dummy secrets at runtime so pattern text never appears contiguously (self-scan invariant).
- **verify_*.py are mandatory CI gates**: `verify_dash.py`, `verify_submit_encoding.py`, `verify_activity_filter.py`, `verify_agent_inspector.py`, `verify_prboard.py`, `verify_failure_drilldown.py`, `verify_wave_telemetry.py`, `verify_dispatch_panel.py`, `verify_scorecards.py`, `verify_ui_trio.py`, `verify_cost_panel.py`, etc. are required pre-push gates; use `--allow-skip` only in truly browserless environments (CI must run all).
- **lock.mjs is the ONLY lock implementation**: never reimplement locking in `proposals.mjs` or elsewhere; all proposals/state updates must use fail-closed `lock.mjs` with exponential backoff + stale-lock breaking.
- **Sanctioned sibling-import guard**: a tool importing another tool by bare name (`from merge_train import gh`) must be preceded by an UNCONDITIONAL module-level `sys.path.insert(0, <tools dir>)`. The `if str(d) not in sys.path:` variant guards identically at runtime but is invisible to `sibling_import_check.py`, which only recognizes a top-level `sys.path.insert(...)` statement — that mismatch is what left `merge_queue.py` red on main and reddened unrelated PRs. A module body runs once, so the unconditional form cannot accumulate duplicate entries. `from tools.X import ...` and imports inside `try/except ImportError` also count as guarded.
- **An import fallback must never silently disable a check**: `toolchain_health.py` imported a symbol (`StateReadAPI`) that does not exist in `state_store.read_api` (the class is `ReadAPI`), so its `except ImportError: ... = None` fallback fired on every run and both heartbeat checks were permanently reported "unavailable" instead of evaluated — dead from the day it was written. Any `try: import ... except ImportError: X = None` seam needs a test asserting the real symbol resolves AND that the dependent code path actually runs with it (see `tests/test_toolchain_health.py::TestStateAPIImportIsLive`).

## Tool index

The per-tool one-liner index lives in `tools/INDEX.md`, generated from each tool's
`INDEX:` docstring/header line — NOT hand-maintained here (that inline list was the
top merge-queue conflict surface, since every tool-adding PR edited it). To document
a new or changed tool, edit that tool's own `INDEX:` line and run
`python tools/gen_tool_index.py --regenerate`. A tool with no `INDEX:` line fails
closed. `claudemd_lint.py` enforces that `tools/INDEX.md` is byte-identical to the
generator output (hand-edits are rejected).

## Gates & tests
- `secret_scan.py --staged` — pre-push gate (exit 0=clean/1=findings/2=error; `# secretscan: allow-pattern-docs` pragma)
- `agent-forensics.sh <commit>` — incident/behavior forensics, read-only git plumbing; `--diff <A> <B>` for rules/docs diff
- **Python**: `npm run test:py`; **Shell**: `bash -n tools/*.sh && shellcheck tools/*.sh`; **Node**: `node --check tools/*.mjs`
- **Subprocess encoding (G10)**: every `subprocess.run`/`Popen` decoding output passes explicit `encoding='utf-8'`; the platform default is cp1252 on Windows and corrupts non-ASCII output. `encoding_lint.py` scans the WHOLE repo, so one violation anywhere blocks every Python-touching push.
