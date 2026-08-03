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
- **Lint evasion (G11)**: `verify_no_lint_evasion.py` flags compile-time string construction that hides another gate's trigger token — adjacent-literal `+` chains, all-constant `str.join`, all-constant f-strings (Python via `ast`; `.js/.mjs/.cjs` via regex). Fires only when the RECONSTRUCTED value matches a gate token (word-boundary anchored) AND no single fragment contains that whole token, so for a protected `alpha.json` the form `prefix + 'alpha' + '.json'` is evasion while `prefix + 'alpha.json'` is not (the owning gate still sees the latter). Reports file:line + reconstructed value + matched token; CLI `[--root DIR] [--paths P ...] [--json] [--check]`, exit 0=clean/1=findings/2=error, stdlib-only. Tokens are DERIVED by AST-parsing the `*_TO_PROTECT` tables in `stateapi_lint.py` — never imported and never re-spelled as literals here, since spelling them would make the detector itself a violation of the gate it protects — plus built-in ratchet-baseline filenames. Sanctioned exemptions, deliberate and not to be "fixed": runtime-assembled dummy credentials (splitting those is a REQUIRED invariant, so credential-placeholder-shaped values are skipped), `tests/**/fixtures/` trees, and `# lint-evasion-ok` / `// lint-evasion-ok` on any line of the construction. NOT yet wired into CI, so this entry deliberately does not claim wired-gate status (see the module docstring for that labelling rule): the first real-tree run found a live escape (`health_checks.py` splits two heartbeat filenames, commit 16b3f8e3, after which the stateapi baseline was ratcheted down 39->37); remediation needs facade routing plus a baseline change, so until then the tool exits 1 on the tree and `tests/test_verify_no_lint_evasion.py` pins the known-escape set as a bidirectional ratchet. Wire into `ci.yml` only once that set is empty.
