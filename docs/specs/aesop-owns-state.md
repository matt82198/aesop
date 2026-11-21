# Spec: aesop-owns-state (F3)

Source plan: C:/Users/Mattt/Desktop/aesop-chat-plan.md sections 0, 1, 2, 4 (M0-M5). Authorization: Matt 2026-11-21, "all state currentlyy lives in conductor3 which is awful, aesop is literally the manager".
Frozen original: `aesop-owns-state.original.md` (byte-identical, never edited).

## Goal

Move ownership of operational state (inbox, heartbeats, `.HALT`, usage-watchdog kill list, signals, monitor outputs, ledger) from conductor3 to aesop, in six steps M0 to M5, without ever breaking a running hook or daemon. WHY: ownership is inverted today (the manager, aesop, reads its own liveness from the mirror, conductor3). The migration is add-first, dual-write, switch readers one family at a time, soak, then stop old writes; nothing is deleted and no hook is loosened (Cardinal Rule 1). Safety-signal files (`.HALT`, `usage-watchdog.kill.json`, heartbeats) fail closed: a missing value is MISSING/UNKNOWN, never ALIVE or "no kills".

## Decisions

- State root (RESOLVED 2026-11-21, Matt): the single state root is the aesop repo `state/` directory (git-ignored, the default `AESOP_STATE_ROOT`). See `docs/specs/README.md` section Decisions. Consequence: brain hooks that live outside any repo reach aesop state through the resolver registry or the `AESOP_STATE_ROOT` variable, never through a second shared root such as `~/.aesop/state`.

## Non-goals

- No deletion of any conductor3 file; old paths remain as frozen history/mirror.
- No hook, settings.json, or gate is removed, loosened or made fail-open; hook edits only change path resolution, via brain-worktree PRs, tests updated first.
- No migration of `bash-history/` contents (prune plus env override instead), nor of the `brain/`, `scripts/` mirrors.
- No chat features (F1, F2).
- No big-bang cutover; each step is separately revertable.
- No second state root outside the aesop repo.

## Requirements

- F3-R1 (M0): `tools/state_paths.py` exposes `resolve(name) -> {primary, legacy}` and `--check` exits 0 only if every hardcoded `conductor3/state|monitor|ledger` path in `~/.claude`, `~/scripts` and aesop is registered. Check: fixture with an unregistered literal makes `--check` exit 1 (A1, mutation proof).
- F3-R2 (M0): The generated reader/writer table matches section 1 of the plan with no unlisted path. Check: diff of generated table vs registry fixture is empty (A2).
- F3-R3 (M1): The canonical root is the aesop repo `state/` directory, overridable by `AESOP_STATE_ROOT`; with `AESOP_STATE_ROOT` set to a fresh temp dir, all five streams (`inbox`, `signals`, `ledger`, `monitor_actions`, `proposals`) are created, a rerun is idempotent, and no file is written outside that root. Check: A3 with isolation tripwire green.
- F3-R4 (M1): Zero behavior change: existing suites pass unchanged with executed count equal to declared and 0 dormant. Check: runner summary compared before and after (A4).
- F3-R5 (M2): Each migrated writer writes the aesop root first, then the conductor3 legacy path, best-effort; a legacy-side failure is logged and not raised. Check: per-writer test with a read-only legacy dir shows the aesop write present, a log line, and no exception (A5).
- F3-R6 (M2): For `.HALT` and `usage-watchdog.kill.json`, a failure on either side returns non-zero (fail closed). Check: A5 for both files with each side failing in turn; every case exits non-zero.
- F3-R7 (M2): `usage-kill-switch.mjs` still denies when a kill entry exists only in the legacy file. Check: existing hook test unchanged and green (A6).
- F3-R8 (M2): Writer migration order is low risk first (pr_sweep, backup-fleet, cron logs, heartbeats, inbox, ledger, then usage-watchdog and `.HALT`), recorded as an `order` field in the registry. Check: test asserts `.HALT` and `usage-watchdog.kill.json` hold the highest `order` values.
- F3-R9 (M3): Readers use the aesop primary when present; primary absent and legacy present uses legacy plus a logged NOTICE; both absent returns MISSING/UNKNOWN, never ALIVE and never "no kills". Check: three-case test per reader family (A7).
- F3-R10 (M3): No hook is deleted, loosened or made fail-open; `read-gates-failopen` and `_failclosed` suites are green on the brain PR with denial text byte-identical. Check: A8 literal suite output.
- F3-R11 (M3): Reader switch order is `mcp/server.mjs` and `ui/config.py`, then `tools/*`, then `~/scripts/*`, then hooks (`usage-kill-switch.mjs`, `no-polling.mjs`, `claim-binding.mjs`) last, one PR per reader family. Check: PR log lists family and order; each hook PR shows its test red before the fix.
- F3-R12 (M3): `.HALT` is checked in both locations until cutover; presence in either means halted. Check: HALT only in legacy, only in primary, and in both each report halted (three fixture cases).
- F3-R13 (M4): A parity checker compares aesop and conductor3 (heartbeat age bucket, inbox counts, ledger totals) and no step proceeds unless drift is zero for 7 consecutive days. Check: offline test with seeded drift makes the checker exit non-zero; live output recorded (A9).
- F3-R14 (M5): After old writes stop, conductor3 files are present and unchanged, every aesop reader is green, and `state_paths --check` reports no legacy writer. Check: fixture hashes of legacy files identical before and after (A10).
- F3-R15 (M5): conductor3 `CLAUDE.md` is amended to "mirror only; aesop owns state". Check: grep test over a fixture of the amended conductor3 `CLAUDE.md` finds the phrase.
- F3-R16 (all steps before M5): Each step is independently revertable; reverting a reader PR restores prior behavior with conductor3 still current. Check: revert-simulation test on one reader family restores prior output.

## Risks

- Many readers of heartbeats; a stale-beat read is a safety signal and must not default to ALIVE.
- `.HALT` split across two trees fails open if only one is checked.
- Live watcher pids (`clear_on_stop`, `clear_watcher`); moves only at a restart boundary.
- Two diverging copies of `collect-signals.mjs`; aesop's is the single source.
- `cost_ceiling.py` is a safety gate; totals must be identical pre/post migration.
- Foreign-agent paths hardcoded in AGENTS.md; dual-append needed.
- Reader/writer lists were from grep and may be incomplete; M0 must regenerate mechanically.
- Brain hooks outside any repo must locate the aesop checkout to find state; a wrong or missing checkout must fail closed (MISSING/UNKNOWN), never fall back to an empty root.

## Open questions for Matt (verbatim from the plan)

1. (BLOCKING, RESOLVED 2026-11-21) "Cutover home: should aesop's `state/` (git-ignored, per-machine) be the canonical root, or should it be a shared location like `~/.aesop/state` so the brain hooks (outside any repo) can read it without knowing the repo path?"
   Matt's answer: "aesop repo state/". The single state root is the aesop repo's state/ directory (git-ignored, the default AESOP_STATE_ROOT).
2. (not blocking) "Should conductor3 be retired entirely after M5 (watchdog, monitor, ledger code all living in aesop), or kept as a thin private mirror of `~/.claude` and `~/scripts` only?" Only affects post-M5 (F3-R15 wording). Open.
3. (not blocking) "Are foreign agents (Codex, agy) allowed to be told a new inbox path (AGENTS.md currently hardcodes `conductor3/state/ui-inbox.md`), or must the old path keep accepting appends indefinitely?" Default: old path keeps accepting appends (dual-append) until answered. Open.
