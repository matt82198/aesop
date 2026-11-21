# Spec: aesop-owns-state (F3)

Source plan: C:/Users/Mattt/Desktop/aesop-chat-plan.md sections 0, 1, 2, 4 (M0-M5). Authorization: Matt 2026-11-21, "all state currentlyy lives in conductor3 which is awful, aesop is literally the manager".
Frozen original: `aesop-owns-state.original.md` (byte-identical, never edited).

## Goal

Move ownership of operational state (inbox, heartbeats, `.HALT`, usage-watchdog kill list, signals, monitor outputs, ledger) from conductor3 to aesop, in six steps M0 to M5, without ever breaking a running hook or daemon. WHY: ownership is inverted today (the manager, aesop, reads its own liveness from the mirror, conductor3). The migration is add-first, dual-write, switch readers one family at a time, soak, then stop old writes; nothing is deleted and no hook is loosened (Cardinal Rule 1). Safety-signal files (`.HALT`, `usage-watchdog.kill.json`, heartbeats) fail closed: a missing value is MISSING/UNKNOWN, never ALIVE or "no kills".

## Non-goals

- No deletion of any conductor3 file; old paths remain as frozen history/mirror.
- No hook, settings.json, or gate is removed, loosened or made fail-open; hook edits only change path resolution, via brain-worktree PRs, tests updated first.
- No migration of `bash-history/` contents (prune plus env override instead), nor of the `brain/`, `scripts/` mirrors.
- No chat features (F1, F2).
- No big-bang cutover; each step is separately revertable.

## Requirements

- F3-R1 (M0): `tools/state_paths.py` is the single resolver `resolve(name) -> {primary, legacy}` and `--check` exits 0 only if every hardcoded `conductor3/state|monitor|ledger` path in the three trees (`~/.claude`, `~/scripts`, aesop) is in the registry. Check: fixture with an unregistered literal makes it exit 1 (A1, mutation proof).
- F3-R2 (M0): The generated reader/writer table matches section 1 of the plan with no unlisted path. Check: diff of generated table vs registry fixture is empty (A2).
- F3-R3 (M1): `AESOP_STATE_ROOT` is canonical; a fresh temp root gets all files and streams (`inbox`, `signals`, `ledger`, `monitor_actions`, `proposals`), idempotent on rerun, no write outside the root. Check: A3 with isolation tripwire green.
- F3-R4 (M1): Zero behavior change: existing suites unchanged, executed count equals declared, 0 dormant. Check: A4 runner summary compared before/after.
- F3-R5 (M2): Every migrated writer writes aesop first, then conductor3, best-effort; a conductor3-side failure is logged, not raised. Check: per-writer test with a read-only legacy dir (A5).
- F3-R6 (M2): For `.HALT` and `usage-watchdog.kill.json` a one-side failure returns non-zero (fail closed). Check: A5, both files, each side failing in turn.
- F3-R7 (M2): `usage-kill-switch.mjs` still denies on a kill entry in the legacy file. Check: existing hook test unchanged and green (A6).
- F3-R8 (M2): Writer order follows low risk first (pr_sweep, backup-fleet, cron logs, heartbeats, inbox, ledger, then usage-watchdog and `.HALT`). Check: migration checklist in `state_paths` registry carries an `order` field; test asserts `.HALT` and kill.json have the highest order values.
- F3-R9 (M3): Readers use primary when present; primary absent and legacy present uses legacy plus a logged NOTICE; both absent returns the original MISSING/UNKNOWN, never ALIVE and never "no kills". Check: three-case test per reader family (A7).
- F3-R10 (M3): No hook is deleted, loosened or fails open; `read-gates-failopen` and `_failclosed` suites green on the brain PR with denial text unchanged. Check: A8 literal suite output.
- F3-R11 (M3): Reader switch order: `mcp/server.mjs` and `ui/config.py`, then `tools/*`, then `~/scripts/*`, then hooks (`usage-kill-switch.mjs`, `no-polling.mjs`, `claim-binding.mjs`) last, one PR per reader family. Check: PR log review lists family and order; each hook PR shows its test red first.
- F3-R12 (M3): `.HALT` is checked in both locations until cutover; either present means halted. Check: HALT in only legacy, only primary, and both each yield halted.
- F3-R13 (M4): Parity checker compares aesop and conductor3 (heartbeat age bucket, inbox counts, ledger totals) and reports 0 drift for 7 consecutive days; no step proceeds on drift. Check: A9 literal output; offline test with seeded drift fails.
- F3-R14 (M5): After old writes stop, conductor3 files are present and unchanged, every aesop reader green, and `state_paths --check` shows no legacy writer. Check: A10 hash of legacy files before/after.
- F3-R15 (M5): conductor3 `CLAUDE.md` is amended to "mirror only; aesop owns state". Check: grep for the phrase in the conductor3 PR.
- F3-R16: Rollback: at every step before M5, reverting the reader PR leaves conductor3 current. Check: revert-simulation test for one reader family restores prior behavior.

## Risks

- Many readers of heartbeats; a stale-beat read is a safety signal and must not default to ALIVE.
- `.HALT` split across two trees fails open if only one is checked.
- Live watcher pids (`clear_on_stop`, `clear_watcher`); moves only at a restart boundary.
- Two diverging copies of `collect-signals.mjs`; aesop's is the single source.
- `cost_ceiling.py` is a safety gate; totals must be identical pre/post migration.
- Foreign-agent paths hardcoded in AGENTS.md; dual-append needed.
- Reader/writer lists were from grep and may be incomplete; M0 must regenerate mechanically.

## Open questions for Matt (verbatim from the plan)

1. (BLOCKING) "Cutover home: should aesop's `state/` (git-ignored, per-machine) be the canonical root, or should it be a shared location like `~/.aesop/state` so the brain hooks (outside any repo) can read it without knowing the repo path?" Blocks F3-R3 and every later requirement (the root path is the registry's primary).
2. (not blocking) "Should conductor3 be retired entirely after M5 (watchdog, monitor, ledger code all living in aesop), or kept as a thin private mirror of `~/.claude` and `~/scripts` only?" Only affects post-M5 (F3-R15 wording).
3. (not blocking) "Are foreign agents (Codex, agy) allowed to be told a new inbox path (AGENTS.md currently hardcodes `conductor3/state/ui-inbox.md`), or must the old path keep accepting appends indefinitely?" Default: old path keeps accepting appends (dual-append) until answered.
