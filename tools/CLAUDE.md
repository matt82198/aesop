# tools/ — Build utilities

Local-only Python (stdlib only, no external deps), bash (POSIX, CRLF-safe).

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only; dummy secrets are runtime-concatenated, never literal.
- In worktrees use ABSOLUTE paths under the worktree for every write; redaction and path-handling code must genericize over Windows profile names (use `[A-Za-z0-9_]+` regex, not hardcoded) so checkout works across shared boxes.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## Core invariants
- **Never print secrets**: mask as pattern name + masked value only; NEVER output raw credentials/tokens.
- **AESOP_STATE_ROOT**: all heartbeat/ledger/logs use `AESOP_STATE_ROOT` env var (default `./state`) or CLI args; no hardcoded personal paths.
- **halt.py state-dir default**: `resolve_state_dir()`'s precedence is `AESOP_STATE_ROOT` env > `aesop.config.json` `state_root` > `$AESOP_ROOT/state` (when `AESOP_ROOT` is set) > `common.get_state_dir()`'s cwd-relative `./state`. The `$AESOP_ROOT/state` step (fixed 2026-10-05, PR #773) matters because callers that `cd` elsewhere before invoking `halt.py --status` (e.g. `daemons/run-watchdog.sh`'s single-source-of-truth delegation) must still find a sentinel written at `$AESOP_ROOT/state/.HALT` — a bare cwd fallback silently missed it and let a halted daemon keep running.
- **Fragment-assembled secrets in tests**: `scanner_selftest.py` concatenates dummy secrets at runtime so pattern text never appears contiguously (self-scan invariant).
- **verify_*.py are mandatory CI gates**: `verify_dash.py`, `verify_submit_encoding.py`, `verify_activity_filter.py`, `verify_agent_inspector.py`, `verify_prboard.py`, `verify_failure_drilldown.py`, `verify_wave_telemetry.py`, `verify_dispatch_panel.py`, `verify_scorecards.py`, `verify_ui_trio.py`, `verify_cost_panel.py`, `verify_cost_summary_drawer.py`, etc. are required pre-push gates; use `--allow-skip` only in truly browserless environments (CI must run all).
- **lock.mjs is the ONLY lock implementation**: never reimplement locking in `proposals.mjs` or elsewhere; all proposals/state updates must use fail-closed `lock.mjs` with exponential backoff + stale-lock breaking.
- **state_rebuild.py --check is the tracker.json drift gate, not a proxy**: it diffs disk against the canonical materializer, so any tracker writer (e.g. `tracker_guard.py`) must close items through the sanctioned write facade — direct `tracker.json` patches are correct only until the next unrelated write re-renders the whole file from the event log (GAP, 2026-10-05).
- **Conflict marker gate (Guardrail G14, 2026-10-06)**: `conflict_marker_check.py` scans tracked text files for literal unresolved markers (`<<<<<<<`/`=======`/`>>>>>>>`/diff3 `|||||||`) via `--check` (full tree; wired as a CI gate, ci.yml shard-0) or `--staged RANGE` (added-lines-only diff, pre-push hook); skips binaries + `tools/.conflict-marker-allowlist.json` paths, honors inline `# conflict-marker-ok`; exit 0=clean/1=findings/2=error. Closes the PR #834 gap: merge commit b2c4db77 landed a literal `<<<<<<< HEAD` block in this very file and claudemd_lint/claudemd_sync_gate/all PR CI passed because none of them scan for conflict markers.
- **power_selftest.py scanner check fails closed (2026-10-05)**: a check that cannot locate/run/parse `scanner_selftest.py` renders `scanner:FAIL`, never `scanner:None`/`n/a`/OK -- a silently-broken scanner gate must never read healthy. Every check result funnels through `render_segment()`, which treats a `None` result or an OK/WARN `DETAILED_OK_CHECKS` (`decisions`/`scanner`/`trigger`) result whose `details` is `None` as `FAIL:unevaluated`. Tests asserting the overall exit code (e.g. `TestPowerSelftestHookDetection`) must pin `SCRIPTS_ROOT` at a real `scanner_selftest.py` so the scanner check is deterministic regardless of whether the CI runner's `$HOME` happens to carry `~/scripts` -- otherwise an unrelated check flips the exit code these tests assert on.
- **Halt kill-switch enforcement**: `merge_train.py` gates ALL entry points and merge actions with `halt.py` checks; halted tool exits 1, import failure exits 2 (FAIL CLOSED). `halt.py` provides the public API: `is_halted()` / `get_halt_info()` / `clear_halt()` / `resolve_state_dir()` (respects `AESOP_STATE_ROOT` env > config `state_root` > default). Sentinel location: `<state_dir>/.HALT` (JSON). Do not edit the sentinel manually; use `halt.py set/--clear` CLI.
- **Merge-queue daemon (merge_queue.py) self-heals on crash**: when a pass crashes mid-batch and leaves the shared worktree checked out on an `integrate/q-*` branch, the next pass detects this in `worktree_is_safe()` and automatically reparks to main before proceeding. No manual intervention needed; stalled passes recover transparently.
- **Inert-code cleanup (2026-10-06, audit #2)**: `state_md_verifier.py` had an unreachable duplicate `return` after the real one; `humanize_lint.py` imported `CLIBuilder`/`OutputFormatter`/`Callable` and never used any of them; `audit_report.py` imported `Path` unused. `test_battery.py`'s `AESOP_BATTERY_HARNESS_TIMEOUT_S`/`AESOP_BATTERY_LOGDIR` env overrides had no setter anywhere (no workflow/script/settings.json) and the tool itself has no automation caller — removed in favor of the hardcoded defaults they always resolved to. Verified each via `pyflakes`/`grep` before touching; `audit_report.py --strict` was checked and kept (implemented, tested, documented — just not yet wired into any CI gate, which is a feature decision, not dead code).

## init_project.py — Worktree support

- **Worktree .git handling**: `resolve_real_git_dir()` detects when `.git` is a FILE (worktree case) and uses `git rev-parse --git-common-dir` to locate the actual git directory.
- **Fallback git dir resolution**: Manual parsing of `.git` file's `gitdir:` pointer if git command fails.
- **Hook installation**: `install_pre_push_hook()` uses resolved git dir to place hooks in the common directory (not worktree), preventing ENOTDIR errors in worktree scenarios.
- **Security**: symlink checks preserved throughout resolution.
## Tool index

Full one-liner index of every tool in this directory: see `tools/INDEX.md` (generated
by `tools/gen_tool_index.py --regenerate` from each file's own `INDEX:` header line;
never hand-edit it -- the byte-identity gate rejects drift). This file stays navigation
only so a tool-adding PR never conflicts with every other in-flight PR over the same
inline list (that conflict-magnet is why PR #751 moved the index out of here). Index
merges through the `aesop-regen` driver (`.gitattributes` -> `generated_merge.py`,
registered per clone by `install_merge_drivers.py`; the pre-push hook does that for you):
a structured 3-way merge of entries rendered through the generator, so two PRs each adding
a tool merge to an already-regenerated index. (git's `union` kept both sides verbatim and
left the index unsorted/duplicated on every merge-from-main: #784/#856/#739.) Rule:
merge-from-main = merge, then `python tools/gen_tool_index.py --regenerate && git add
tools/INDEX.md`, or let the driver/gate do it — `generated_push_gate.py` (hook
`check_generated_regen()`) verifies the COMMITTED bytes of every registered regenerable
artifact (`generated_paths.REGISTRY` entries with a `regen` argv; must equal
`merge_queue.REGENERATORS`) at the pushed tip in a throwaway worktree and rejects a stale
one with exactly that instruction; the driver's per-worktree `.needs-regen` stamp
(`git rev-parse --git-path aesop-needs-regen`) forces the check even on an empty range.

## Adding a new gate (2026-10-06, PR #872 postmortem)

Adding or wiring a pre-push gate took PR #872 five red CI rounds -- a different
checklist item each time. Two fixes: `gate_stub_list.py` derives
`tests/test_pre_push_policy.sh`'s TTY-fixture stub list from the real `check_*`
functions in `hooks/pre-push-policy.sh` (parses for `gate_tool_status()` call
sites) instead of a hand list a new gate is never automatically added to --
see `tests/test_gate_stub_list.py` for the red-first proof a dummy `check_zzz`
is picked up. `new_gate_check.py` runs the whole checklist in one command
(pre-push self-test, `gate_inventory.py` axis2 parity, `claudemd_lint.py`
working-tree + `--headroom`, `claudemd_sync_gate.py`, `portability_check.py`
ratchet, `verify_gates_wired.py`, `dispatch_lint.py`, `conflict_marker_check.py`,
and a dry pre-push range check against `origin/main` with no real push) and
prints the exact fix command for any red row; exit 0=all green/1=any red/2=usage
error. Allowlisted in `tools/gate-inventory-allowlist.json` (operator-invoked
meta-tool, no file-content rule of its own to wire into CI). See
`LANE-CONTRACT.md` section 5.

The dry-range row (2026-10-06 follow-up): a branch that merged `origin/main`
legitimately carries changes to registered generated paths (`generated_paths.py`
`REGISTRY`, e.g. `tools/INDEX.md`) whose regeneration is the hook's designed
writer path (`AESOP_ALLOW_GENERATED=1`) -- running the real hook without that
var set made this row false-red (9/10 on a clean branch, PR #882). The row now
pre-checks changed registered paths against their generator's own `--check`
(`GENERATED_FRESHNESS_CHECKS` in `new_gate_check.py`); fresh -> PASS with the
escape hatch set for the dry run; stale -> FAIL with the exact regen command,
never reaching the hook.

## Recent additions (2026-10)

- `generated_merge.py` / `generated_push_gate.py` / `install_merge_drivers.py` — the
  generated-artifact merge triangle (see § Tool index): regenerating merge driver, committed-
  bytes pre-push gate, idempotent per-clone driver registration. Tests:
  `tests/test_generated_merge.py` (git integration incl. the `union` negative control,
  3-way entry semantics, stamp, registry/queue agreement), `tests/test_generated_push_gate.py`
  (stale-commit rejection with the one-line instruction, dirty-tree-does-not-rescue, stamp
  consumption, sourced-hook wiring).

- `linux_shape_check.py` — WSL-based cross-platform test gate: detects commits touching
  shell/workflow/Node files, runs test suites under WSL to catch Windows-only CI reds
  (e.g., isolated-home USERPROFILE assumptions, shell failures on Ubuntu). Wired into
  pre-push-policy.sh after generated_paths check. Tests: 17 unit cases covering skip/
  notice/fail/require-wsl scenarios.

## Gates & tests
- `secret_scan.py --staged` — pre-push gate (exit 0=clean/1=findings/2=error; `# secretscan: allow-pattern-docs` pragma)
- **Receipt gate (measurement period)**: `emit_receipt.py --post` signs a local run (Ed25519 key at `$AESOP_RECEIPT_KEY`, pub `receipt_pubkey.pub`); `verify_receipt.py` recomputes tree hash + freshness; `verify-receipt.yml` non-required. `verify_receipt.py --fetch-for-head SHA --repo-slug OWNER/REPO` (backed by `fetch_receipt_for_head()`) is the Action's own lookup step — it NEVER raises; any lookup failure (gh api error, malformed payload, this module absent on a stale PR tree) degrades to "no receipt found" (absent), never a crash, since only a receipt actually found-and-invalid may fail the job (2026-10-06 incident: #856/#857 showed FAILURE from an uncaught ModuleNotFoundError in the old inline fetch script). See docs/RECEIPT-GATE.md.
- **Workflow validity (GAP 2026-10-06)**: `ci_workflow_lint.py` checks GitHub schema SEMANTICS, not just YAML — `strategy` keys, `matrix.<k>`/`exclude` resolution, `needs:` targets, `runs-on` form, and per-key context availability (`shell:` allows none) — and runs actionlint when found (`$ACTIONLINT_BIN`/PATH; `CI_WORKFLOW_LINT_REQUIRE_ACTIONLINT=1` fails closed, ci.yml sets it). A PyYAML-valid main-full.yml with `exclude:` beside `matrix:` ran ZERO jobs for ~10 merges while this gate was green. Escape signature for the detector: run `conclusion == failure` with `jobs == []` and `name == path` = WORKFLOW-INVALID.
- `agent-forensics.sh <commit>` — behavior forensics; `--diff <A> <B>` for rules/docs diff
- **Python**: `npm run test:py`; **Shell**: `bash -n tools/*.sh && shellcheck tools/*.sh`; **Node**: `node --check tools/*.mjs`
- **Subprocess encoding (G10)**: every `subprocess.run`/`Popen` decoding output passes explicit `encoding='utf-8'`; the platform default is cp1252 on Windows and corrupts non-ASCII output. `encoding_lint.py` scans the WHOLE repo, so one violation anywhere blocks every Python-touching push. Same trap hits argparse `--help` text: a Unicode arrow/dash in a `help=`/`description=` string crashes `print_help()` on a stock cp1252 console (not caught by `encoding_lint.py`, which only checks `subprocess`/`open`) — keep all argparse-printed text plain ASCII (`->`, `-`); fixed 2026-10-05 in `auto_merge.py` + 7 other tools' `description=` strings.
- **Dead-baseline liveness (GUARDRAIL #3)**: `baseline_liveness_check.py` finds each `.*-baseline.json` ratchet baseline's consumer (hardcoded usage in `tools/*.py`, or `--baseline` wiring in `.github/workflows/*.yml`/`hooks/*.sh`) and re-runs that consumer's own `--baseline FILE --json` check to surface entries it no longer finds (stale allowance hiding a new violation); a baseline with no consumer is DEAD, fail-closed. `--prune` drops exactly the stale entries (shrinks counts, never raises them). CLI `[--root DIR] [--prune] [--json]`, exit 0=clean/1=dead or stale/2=error, stdlib-only; wired into `ci.yml`.
- **Pyflakes ratchet gate (Guardrail G15, 2026-10-06)**: `pyflakes_gate.py` wraps `python -m pyflakes` over `tools/ bin/ ui/ state_store/ driver/ monitor/ daemons/ tests/ bench/`, keying findings as `file@MessageClassName` (e.g. `UnusedImport`, `UnusedVariable`) against the committed `.pyflakes-baseline.json` (same bidirectional exact-match ratchet as `.portability-baseline.json`); fails closed on a NEW finding, fails closed on a STALE baseline entry (a fix must regenerate the baseline in the same PR), and fails closed (exit 2) if the dev-only pyflakes package is not installed. `--update-baseline` is review-only; CI never passes it. Closes a ~900-finding unused-import/unused-variable backlog that accumulated because nothing had ever run pyflakes in CI; the remaining baselined findings are deliberately-frozen `bench/seam_tasks/` ground-truth fixtures, guarded re-export/import-probe patterns (`# noqa: F401`/`# noqa: F841` with a reason), and out-of-scope categories (e.g. `FStringMissingPlaceholders`). CLI `[--check] [--json] [--paths DIR...] [--root DIR] [--baseline FILE] [--update-baseline]`, exit 0=clean/baselined, 1=new-or-stale findings, 2=error; wired into `ci.yml` shard 0.
- **Dispatch policy linter (G12)**: `dispatch_lint.py --check` enforces merge automation and lane protocol rules in agent dispatch prompts — detects forbidden patterns (`gh pr merge`, `--admin`/`--auto`/`--no-verify`/`--force`, `git stash`, credential hunting) and lane-side CI polling (`ci_merge_wait`, `gh run watch`, `merge_train.py`, sleep-wrapped `gh pr checks`). Lane terminal action: push → open PR → `gh pr edit <n> --add-label merge-queue` → exit (merge-queue advancer owns CI-wait). Excludes test files from scanning; filters documentation strings and markers (`# dispatch-ok`, `// dispatch-ok`, `<!-- dispatch-ok -->`). CLI `[--check] [--fix] [--json] [PATH]`, exit 0=clean/1=violations/2=error, stdlib-only; wired into pre-push gates as a security check.
- **Spec contract validator (G4)**: `spec_contract_validator.py --check` AST-scans agent-dispatch call sites (`agent()`/`Agent()`/`Task()`/`subagent_type=`/`agentType=`) for forbidden bypass flags, credential-hunting + env-var allowlist violations, missing isolation markers on file-writing prompts, and advisory role-routing (unknown specialist types); `# contract-ok` inline comment suppresses a call site; CLI `[--check]` | `[--json]` | `[--paths DIR_OR_FILE...]` | `[--root PATH]`, exit 0=clean/1=findings/2=error, stdlib-only.
- **Lint evasion (G11)**: `verify_no_lint_evasion.py` flags compile-time string construction that hides another gate's trigger token — adjacent-literal `+` chains, all-constant `str.join`, all-constant f-strings (Python via `ast`; `.js/.mjs/.cjs` via regex). Fires only when the RECONSTRUCTED value matches a gate token (word-boundary anchored) AND no single fragment contains that whole token, so for a protected `alpha.json` the form `prefix + 'alpha' + '.json'` is evasion while `prefix + 'alpha.json'` is not (the owning gate still sees the latter). Reports file:line + reconstructed value + matched token; CLI `[--root DIR] [--paths P ...] [--json] [--check]`, exit 0=clean/1=findings/2=error, stdlib-only. Tokens are DERIVED by AST-parsing the `*_TO_PROTECT` tables in `stateapi_lint.py` — never imported and never re-spelled as literals here, since spelling them would make the detector itself a violation of the gate it protects — plus built-in ratchet-baseline filenames. Sanctioned exemptions, deliberate and not to be "fixed": runtime-assembled dummy credentials (splitting those is a REQUIRED invariant, so credential-placeholder-shaped values are skipped), `tests/**/fixtures/` trees, and `# lint-evasion-ok` / `// lint-evasion-ok` on any line of the construction. NOT yet wired into CI, so this entry deliberately does not claim wired-gate status (see the module docstring for that labelling rule): the first real-tree run found a live escape (`health_checks.py` splits two heartbeat filenames, commit 16b3f8e3, after which the stateapi baseline was ratcheted down 39->37); remediation needs facade routing plus a baseline change, so until then the tool exits 1 on the tree and `tests/test_verify_no_lint_evasion.py` pins the known-escape set as a bidirectional ratchet. Wire into `ci.yml` only once that set is empty.
- **CI modes (docs/CI-MODES.md)**: `common.py validate_ci_config()/load_aesop_config()` own the config `ci` block (mirrored code-for-code in `ci_config.js`; `tests/test_ci_config.py` runs both on the same fixtures); `ci_capability.py` is the report-only probe behind `aesop doctor` (Smart App Control / UMCI / WSL / Docker / gh / cloudflared / receipt tools -> mode table; `AESOP_CI_PROBE_FIXTURE` injects probes); `runner_install.py` shares its `runner_blocked()` predicate as preflight; `init_project.py --ci-mode` renders `templates/ci/*.yml` (verify-receipt.yml is a PLACEHOLDER until the receipt lane lands -- the scaffold refuses, never emits it); rendered workflows must pass `ci_workflow_lint.py` + `ci_needs_skip_guard.py` (`tests/test_ci_templates.py`).
- **Node-suite HOME isolation (G8)**: `test_isolation_tripwire.py` is wired around every Node test invocation (`npm run test:node`/`npm test`, and the CI "Run Node.js tests" step), paired with `tests/helpers/isolated-env.mjs` (loaded via `--import`, redirects `HOME`/`USERPROFILE`/`AESOP_SKILLS_HOME`/`AESOP_HOME` to a throwaway temp dir for the whole process) as the structural fix; the tripwire is the independent behavioral proof, snapshotting `~/.claude/{skills,settings.json,memory,hooks}` + global git config before/after and failing closed on any drift. GAP fixed 2026-10-06 (PR #831 shipped both files but wired neither into any Node test invocation, so every suite but one still ran against the real profile); `tests/isolated-home-tripwire.test.mjs` fails red under plain `node --test` (no `--import`) and green under the wired command, proving the wiring instead of just the fixture's existence. **Cross-site drift guard (second GAP, 2026-10-06)**: PR #864 wired the first GAP's fix into TWO sites (ci.yml's ubuntu step, ci.yml's windows-shard raw invocation) but missed a THIRD, pre-existing site -- `main-full.yml`'s own raw `node --test ...` line -- which ran unisolated on every main-full run, staying accidentally green on windows-latest (USERPROFILE is an ambient OS var there) and genuinely RED on ubuntu-latest. `node_harness_wiring_check.py --check` enumerates EVERY Node-suite invocation site across `.github/workflows/*.yml` + `package.json` (resolving `npm run test:node`/`npm test` through to package.json's own script) and fails closed if any site is missing `--import ./tests/helpers/isolated-env.mjs` or the tripwire wrapper -- catching the SHAPE of the gap (partial wiring across sites) so a fourth site drifts loudly instead of silently. Wired into `ci.yml` alongside the other workflow-scanning gates.
- **Templates**: `templates/aesop-dispatch-template.yml` — ready-to-fork GitHub Actions dispatch workflow (workflow_dispatch + cron); adopter guide `docs/ACTIONS-TEMPLATE.md`; verified by `tests/test_actions_template.py` (YAML validity, CLI commands checked against `bin/cli.js --help`, no invented flags/fabricated output). `templates/wave-presets/*.json` is the unrelated wave-manifest-preset set consumed by `wave_templates.py`.
- **wave_manifest_lint.py**: Validates wave manifests for file-ownership disjointness, path existence, prompt sanity, git-history churn heuristics, and per-item testCmd binary resolution. Binary resolution now skips leading shell negation (`!`) and environment assignments (`FOO=value`) to find the actual executable; tested against both negation and env-var patterns.
- **PR symbol survival (Guardrail G13)**: `pr_symbol_survival_check.py` collects top-level symbols
  (Python `def`/`class`/`async def`, JS/MJS `export function|const|class` + top-level `function name(`,
  shell `name() {`) ADDED by a PR branch's own non-merge commits and verifies each still exists in the
  final head tree (same file, or anywhere in the repo if renamed — reported as INFO); catches the
  "resolved a merge conflict by taking the other side's whole file" class (PR #745: `build_bisect_batches`/
  `parse_bisect_lineage`/`bisect_is_exhausted` silently dropped by a conflict-resolution merge, CI stayed
  green). CLI `--base REF` (default `origin/main`) `--head REF` (default `HEAD`) `--original-head SHA`
  `--json`; exit 0=survived/1=missing/2=error; read-only, stdlib-only. Wired into `ci.yml` as a
  non-blocking (`continue-on-error: true`) advisory job that prints findings to the job summary.
- **pr_sweep.py (STATE.md item 9 residual, PR #871 gap)**: the session-independent PR actor -- arms missing native auto-merge, nudges BEHIND branches (capped at 2/run, oldest first, 20-min throttle), and signals stuck-red (required check failing, head >30 min old) / DIRTY PRs as `pr.red`/`pr.dirty` rows to `<conductor_root>/state/signal-hub-queue.jsonl` (`common.get_conductor_root()`, never a hardcoded path), deduped on `(pr, head, type)`. Never rebases/pushes/merges. Invoked on a 15-min throttle from the watchdog daemon so the nudge does not depend on a live orchestrator session noticing via the Monitor tool.
- **shadow_adjudication.py**: `build_finding_context_pack(enriched=True)` attaches evidence via `driver/context_pack.py`'s `add_evidence_to_pack()` (pure in-memory, never touches the filesystem or repo_root/conductor_root) — never re-derive the already-built brief through `build_context_pack()` with a fake `brief:<name>` source: that resolves the placeholder name as a literal relative path against the process cwd, which only happens to equal repo_root by coincidence (2026-10-06 incident: a prior test's tearDown leaking a polluted process cwd made that placeholder resolve outside the allowlist and raise ContextPackViolation for a perfectly legitimate in-memory brief).
