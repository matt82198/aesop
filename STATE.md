# STATE — Durable System Checkpoint

**What this file is:** The live durable checkpoint that Aesop itself uses during its own `/buildsystem` loop. It records the current system version, architectural decisions, known limitations, and the next milestone. This is not historical archive; it is read by the orchestrator to understand operational state.

**Current Version:** v0.9.2 (PRE-PUBLISH: package.json 0.9.2 on origin/main since #978 merged 2026-10-15 01:54Z; tag v0.9.2 is NOT cut yet; latest origin tag v0.9.1 = 5026195d; release is USER-gated). Previous: v0.9.1 (tag 5026195d, cut 2026-10-09; GitHub release 2026-10-10; npm @matt82198/aesop 0.9.1). v0.9.0 (2026-10-07).

## CURRENT (2026-10-15 late-wave checkpoint 03:00Z)

**Late-wave checkpoint.** Every fact below was verified with `gh`/`git` at 2026-10-15 ~03:01Z on the Mattt box (shas as observed; PR states move, re-verify before acting). Supersedes the 2026-10-14 mid-wave block. Ledger: BUILDLOG.md "2026-10-15 03:00Z late-wave checkpoint".

- **origin/main = a6dce61d** (#993, merged 02:54:16Z). **main-full is RED.** The last 5 completed main-full runs on main are all `failure`: edf028db (02:22Z), c43e156e (02:26Z), 0fffaa2c (02:31Z), 9cf9202f (02:41Z), a6dce61d (02:54Z, run 38673452130). Last green main-full: 2c59d40f (00:59Z).
- **Red cause:** `tests/test_init_project_worktree.py:93` (`test_resolve_real_git_dir_regular_repo`) on windows-latest shard 3/4. AssertionError: the actual path is the long form (`C:/Users/runneradmin/...`) and the expected path is the 8.3 short form (`C:/Users/RUNNER~1/...`). First Windows run after #956's rename.
- **Fix status (verified):** `fix/init-worktree-test-windows-paths` is NOT on origin and has no PR. `feat/new-tests-run-windows-shard` is NOT on origin and has no PR. Neither fix is in flight as of 03:01Z. Main stays red until a fix lane is dispatched and merges.

**Shipped today (aesop: 43 PRs merged 2026-10-15 00:23Z to 02:54Z, `gh pr list --search merged:>=2026-10-14`):** #934 #943 #944 #945 #946 #947 #948 #949 #950 #951 #952 #953 #954 #955 #956 #958 #959 #960 #961 #962 #963 #964 #965 #966 #967 #968 #969 #971 #972 #976 #977 #978 #979 #980 #981 #984 #986 #987 #989 #991 #992 #993 #994.
**claude-scripts** (`matt82198/claude-scripts`) merged: #37 #38 #39 #40 #41 #42 #43.
- #978 = 0.9.2 version bump (origin/main package.json = 0.9.2). #986 = hermetic state_md_verifier tests with the PRE-PUBLISH release window (merged 02:21:09Z).

**Open PRs (11, mergeStateStatus at 03:01Z):**
- DIRTY (conflicts, needs rebase): #932 receipt matrix scoped, #975 ci-lint renamed gates, #982 gates judged by base copy (F-04), #995 bench API offline seam + confirm-spend.
- BLOCKED (checks or review pending), one per line:
  - #957 box-wide watchdog halt.
  - #970 verify-receipt fail-closed.
  - #974 test-isolation tripwire.
  - #983 secret-scan pragma (paired with the landed #987 fix).
  - #985 tools/CLAUDE.md headroom.
  - #988 task-events ingest into state store (head f1bba156).
  - #990 claim-read fail-closed.

**Audit round 1 (2026-10-14, reports in ~/conductor3/audits/2026-10-14-r1/):** 9 lenses. 7 refuter reports on disk. 109 findings in the 7 refuted lenses: 2 REFUTED, 62 CONFIRMED, 37 DOWNGRADED (mostly P1 claims reachable only from operator-only paths), 7 IN-FLIGHT, 1 already filed. The analyst lens (13 findings) has no refuter. orchestrator-drift is analysis only. Lens model caveat: 12 of 24 Opus dispatches were spawned as claude-haiku-5-5 by the harness (feedback receipt f49b6043).

**Audit round 2 (adv-fixers, 2026-10-14 21:42):** 19 merged PRs re-attacked: 11 SOUND, 7 INCOMPLETE, 1 REGRESSION.
- INCOMPLETE fix status: #943 -> fix #986 landed. #950 -> fix #984 landed. #954 -> fix #981 landed. #952 -> fix #994 landed, plus #990 still open (BLOCKED). #949 -> fix #995 still open (DIRTY). #944 (non-ASCII path, multi-commit push) and #966 (test weakness): no fix PR observed.
- REGRESSION #971 (new generic_credential rule silenced by a self-asserted pragma): fix #987 landed 02:21:49Z; its paired pragma fix #983 is still open. The round-2 re-attack has not been rerun on the fix.

**Machinery on the Mattt box (observed):**
- Watchdog heartbeat `~/conductor3/state/.watchdog-heartbeat`: mtime 2026-10-14 22:00:19 CDT (03:00:19Z), read at 03:01Z. Blocked cycles now write the heartbeat and name the blocked repo (#955 MERGED). Watchdog cycle was clean at 21:45 CDT per the orchestrator notes. The scheduled-task query from this shell returned no output, so task state is not claimed here.
- Receipts live from this box: key `00946e62ea15ecd4`, pubkey `tools/receipt_pubkeys/mattt-box-00946e62ea15ecd4.pub` (landed via #965 at 01:16:48Z). Proof PR #972 landed at 01:31:41Z. Fail-closed verify-receipt is #970, still open.
- SQLite task-stream ingest: #988 still open. The existing `state/tracker_events.db` is stale (last event 2026-08-19).
- Brain hooks (reported live in the orchestrator notes, not re-measured here): fail-closed hook harness 29/29; claim-binding Stop hook; task-event projector tags internal vs lane; primary-branch-guard and no-grep hooks rewritten; pile-on hook is state-based.
- CI pacing: at 02:40Z, `gh run list --status in_progress` = 4 and queued = 0. The CI concurrency cap is NOT the pacing factor (earlier claim withdrawn). Pacing is the DIRTY/BLOCKED PRs above.

**Rules recorded 2026-10-14 (durable, from the orchestrator notes):**
- Sync is fetch-only. Explicit `model:"haiku"` on every dispatch. Opus 5.5 for audit lenses only, for this audit.
- All hooks fail closed: malformed or missing stdin, unresolvable cwd or repo, internal exception = deny. WAVE ITEM 19 is the conformance test over every hook.
- A hook denial on the main thread is stop-and-report, never a detour. The only exception is the documented escape token with a logged reason.
- Incidents are recorded through commit trailers (#960), not .md files.
- Gate strengthening proceeds without asking. Gate weakening never does.
- Durable state over narration: every pacing or throughput claim needs `gh run list` counts.
- The GitHub Actions concurrent-job count is the monitored CI cap (/afk decision, reversible; Matt names another cap if he wants one).
- Never restart the user's browser. Chrome bridge: Matt relaunches Chrome himself.
- Orchestrator never runs merge_train.py or any merge-queue polling. Merge trains are dispatched, and the orchestrator verifies `state == MERGED`.

**Release gate (USER-gated, not run):**
- Blocked on main-full green (above). Tag v0.9.2 does NOT exist on origin (`git ls-remote --tags origin`: v0.9.0 = 5dffb81f, v0.9.1 = 5026195d).
- Release = `git tag v0.9.2` then `gh release create v0.9.2`. The release:published workflow publishes to npm via OIDC. Matt's standing order: cut the release once main CI is green.

**Open defects carried (not fixed in this checkpoint):**
- DEFECT 15 (brain): `~/.claude/hooks/session-end-log.mjs` fails schema validation at every session end.
- DEFECT 16 (brain): `no-grep-on-main-thread.mjs` fires for subagents in sibling worktrees.
- DEFECT 18 (brain): `primary-branch-guard.mjs` matches substrings, not the git verb or resolved toplevel.
- Receipts gap: no per-action receipts; intent not bound into the receipt; no zombie-gate kill; no partial receipts; verify-receipt key boundary (F-07) not enforced; meta-gate does not block.
- F-03 gate tamper: CODEOWNERS on enforcement paths is in #969 (MERGED). Required code-owner review needs a second reviewer account (Matt decision).
- Junk files in the primary root (`m.html`, `pp.json`, `q.json`, `r2.json`, `rc.json`, `sub.json`, `subs.json`, `$HOME/`) left untouched under the no-delete rule. Matt decides.
- Box setup: WSL blocked by firmware virtualization (BIOS, Matt action). The Chrome native-host bridge needs Matt to relaunch Chrome.

## Architectural Thesis

Agent behavior is source code. Rules, memory, hooks, and checkpoints live as versioned, portable, diffable filesystem artifacts in git. This design enables code-review, versioning, and forensic replay of orchestration itself—not just the agents' output.

A corollary the 0.7.0 guardrail work makes explicit: a rule written only as prose is not enforced. Every operating rule that matters is expected to become a hook, gate, or linter that fails closed.

The 0.7.1 release added a second corollary: a gate that exists is not a gate that ran. The portability gate sat behind a failing lint step in the same CI job and had never produced a verdict; fixing the earlier step surfaced 9 real violations that had accumulated unseen. Gate *activation* is now its own thing to verify, separately from gate correctness.

## Known Limitations

- **Multi-instance coordination is single-box only.** The 0.7.0 MVP added lease-based SQLite claims (with split-brain and TOCTOU fixes), but claims remain file-system-backed. Multi-box deployment requires a shared lease service; not yet implemented.
- **State-layer consolidation in flight** (git + SQLite + STATE.md are currently three sources of truth; scheduled to collapse into SQLite-as-source + git-as-audit-trail).
- **Benchmark is curated, not sampled** (N=39 judgment tasks, not real-fleet transcripts). Sufficiency is proven; equivalence-to-Opus is not claimed.
- **Documentation gates verify presence, not truth.** A gate that requires a doc to exist induces agents to write one; green means "a doc exists", not "the doc is accurate". Doc claims still need reading against the code.
- **Trigger-layer and box-restore fragility.** Orchestrator-startup recovery (scheduled tasks, conductor3 state clone) is now gated by scheduled-task execution (#787 fix pending in weekly drift PR #790). If scheduled tasks do not fire, trigger layer never runs state updates. Guardrail check proposed (power_selftest trigger-layer check).
- **STATE.md checkpoint staleness.** Live checkpoint was 110+ PRs stale (v0.7.2 era → 2026-08-18 main). Freshness gate proposed; this regeneration sets baseline (2026-08-19).

## Incidents & Fixes (2026-10-07)

- **GIT_DIR env inheritance in receipt matrix (FIXED #921–#926):** git exports GIT_DIR into hook environments; pre-push receipt matrix inherited it, causing test fixtures' `git -C <tmp>` operations to hit the lane repo. Fixture-only trees committed onto lane branches (#912/#921), `git config` writes polluted shared config (dangling core.hooksPath disabled pre-push hooks for hours). Fix = env scrub + throwaway worktree (#924), footprint gate (#922), fixture isolation (#919), hooks-path-sane check.
- **Shared bare-repo core.hooksPath pollution (FIXED):** tests/test_commit_lint fixture left core.hooksPath in bare primary repo's config; pre-push hooks silently inert for hours. Cleared manually; power_selftest hooks-path-sane check added (cardinal rule 8 enforcement); #919 adds fixture isolation scanner + tripwire.
- **Lanes bypassing pre-push via bare-repo push (FIXED):** Lanes using `git -C ~/aesop push` (primary worktree) bypassed hook enforcement. Brain hook deny-default-branch-push now denies it; LANE-CONTRACT.md worktree-only rule reinforced.
- **Checkpoint/clear hooks registered (2026-10-07):** SessionEnd auto-commits control files with secret-scan gate; PreCompact enforces staleness checks with recovery hints. Durable, on-disk rules (session-end-checkpoint.mjs, checkpoint-before-clear.mjs) now enforce state transitions.

## Next Milestone

**Wave-31+:** State-layer multi-instance lifecycle (crash-orphan recovery, liveness detection, monotonic expiry); unsupervised failure-recovery loop; frontier live-run capability; external-benchmark validation.

**NEXT STEPS (post-0.7.2, ranked):**

### Shipped (Most Recent)

- **PR #928 Windows compat fix (ef559043, 2026-10-07) — SHIPPED:** Windows regression from #925: expected content passed to git hash-object stdin with text=True so Python translated LF->CRLF and every comparison reported false drift; fixed by passing UTF-8 bytes.
- **PR #924 emit_receipt throwaway worktree — SHIPPED (2026-10-07):** emit_receipt runs the matrix in a detached throwaway worktree with GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE/GIT_PREFIX scrubbed + caller-tree tripwire; closes fixture-commit and hooksPath escapes structurally.
- **Receipt gate end-to-end proven (2026-10-07) — SHIPPED:** Real lane push with no AESOP_RECEIPT_EMIT/AESOP_RECEIPT_KEY ran full matrix in throwaway worktree, signed ed25519 with default key path, spooled (sha not yet on GitHub), posted as commit comment 203865814 on b893a758; caller tree untouched, core.hooksPath unset, branch carried exactly one commit. Caveat: local receipts report conclusion=failure while two Python shards fail on Windows box; tools/receipt_agreement.py measurement is gate for increment 4.
- **Hard checkpoint+clear enforcement hooks — SHIPPED (brain commits bb31539/00558cf, 2026-10-07):** session-end-checkpoint.mjs (SessionEnd) auto-commits control files with secret-scan gate; checkpoint-before-clear.mjs (PreCompact) enforces staleness checks with recovery hints.
- **LANE-CONTRACT AESOP_ALLOW_GENERATED — SHIPPED (PR #907, 2026-10-07 09:43:49Z):** lanes may deploy generated-paths registry entries without manual approval.
- **Heartbeat-pollution fix — SHIPPED (PR #898, 2026-10-07T02:59:17Z):** daemons/backup-fleet.sh only auto-derives CONDUCTOR_ROOT for the canonical ~/aesop tree, else explicit or skip; new tests/test-backup-fleet-heartbeat-guard.sh.
- **pyflakes unused-import sweep — SHIPPED (PR #890, 2026-10-07T03:24:22Z):** 527 pyflakes findings fixed + tools/pyflakes_gate.py G15 ratchet.
- **Receipt gate increments 1–3 SHIPPED (PR #854, 2026-10-06):** Emit, verify, non-required Action. Increments 4–6 (storage codecs, lineage, signed ledger) remain queued.
- **Automatic receipt emission in pre-push SHIPPED (PR #895, 2026-10-07 09:15:25Z):** feat(receipts): gate 3.5 automatic emission. Linux hang fixes (process-group-safe bound, stub gh/timeout, 60s guards). Receipts LIVE on matt8 box.
- **Receipt signing key provisioned SHIPPED (PR #903, 2026-10-07 09:26:25Z):** chore/receipt proof — Ed25519 key per docs: private ~/.aesop/receipt_key.pem (outside repo), public tools/receipt_pubkey.pub (key_id 8d2a494af2689854). User-scope AESOP_RECEIPT_KEY env var points to key path. Real push emitted and verified.
- **Packaging-portability fixture sharing SHIPPED (PR #901, 2026-10-07 05:36:43Z):** Windows shard-0 180s timeout mitigated via shared scaffold fixture; evidence local-only. Increments 4–6 queued.
- **Watchdog script parity — DONE (2026-10-06, PR #877):** `AesopWatchdogDaemon` scheduled task now runs aesop origin/main's `daemons/run-watchdog.sh --once` from runtime worktree `~/aesop-daemon-runtime` (fetch + reset --hard origin/main each tick, CONDUCTOR_ROOT exported; conductor3 commit 94d23915); heartbeat at `$CONDUCTOR_ROOT/state`; `power_selftest` has WATCHDOG-SCRIPT-PARITY (claude-scripts #32/#33/#36, reports `ok (runtime=<sha>)`); two unattended ticks verified.
- #859, #872: CI-modes product surface — `ci.mode` config, `aesop init --ci-mode`, `aesop doctor` capability table, `aesop runner install|remove` (#859); linux-shape row added to the capability table (#872)
- #872: linux_shape_check gate — distro-aware WSL detection (`wsl -l -q` + `wsl -e true`, not bare `wsl --status`); coexists with G14 in pre-push + CI
- #874: pr_sweep.py — session-independent PR-sweep actor under the 15-min watchdog throttle (closes item 9 residual)
- #870: G14 conflict-marker detection gate (PR #834 incident), wired into pre-push + CI
- #869: main-full.yml Node wiring — isolated-env.mjs into the Node test step (node_harness_wiring_check)
- #871: Merge actor session-independence closed out — dispatch_lint.py no longer contradicts LANE-CONTRACT.md's native-auto-merge policy (item 9, DONE)
- #784: CLI skills installer fails closed; installer fixes its own Windows scaffold gap
- #867: Scheduled-task snapshot diffing (XML fidelity, 5-run stable proof, 4 red-first regression tests)
- #866: dispatch_lint categorical doc exemption (fix for post-#850 red)
- #865: PR symbol survival check (Guardrail G13, non-blocking; promote to blocking after 2026-10-14)
- #864: Node test isolation via isolated-env.mjs
- #863: tripwire hardcoded-path resolution (env-derived conductor root)
- #862: verify-receipt neutral on missing receipt
- #860: pre-push range computation via origin/main (fixes new-branch cases)
- #856: shell-test isolation from conductor3 state
- #855: main-full.yml GitHub schema fix + actionlint (GAP)
- #850: CI throughput: PR head-SHA checkout + Windows path gating
- #883: tools/new_gate_check.py + gate_stub_list.py — one-command new-gate checklist (TTY fixture stub list, inventory parity, claudemd lint, portability, pre-push self-test) shipped 2026-10-07
- #881: LANE-CONTRACT updates — PID-scoped process kills, dirty-PR recovery, claims-need-proof; ship 2026-10-07
- **v0.9.0 release — SHIPPED (2026-10-07 02:25:53Z, PR #886):** GitHub release published via `release:published` event; tag 5dffb81f; 103 PRs merged since v0.8.0. Main-full CI resolved after #882/#897 fixes; npm publish via OIDC triggered (workflow success 2026-10-07T02:25:55Z).
- **tools/INDEX.md union-merge deflake — SHIPPED (2026-10-07 02:30:41Z, PR #882):** Regen driver + committed-bytes push gate; prevents duplicate INDEX.md entries on clean merges; also fixes remote_refs_tripwire false positive; 45/45 + 223/223 regression tests green.
- **Receipt increments 1–3 extended to gates 4–6 (PRs #909–#918, 2026-10-06/07):** Receipt schema_version=1 backward-compatible (#909); verify_receipt added to gate_inventory (#910); tools/receipt_agreement.py measures local vs hosted-CI agreement via check-runs/commit-comments (#911); receipt_common.resolve_receipt_key_path chain (env → ~/.aesop/receipt_key.pem) (#913); pre-push hook probes default path before warning (#916); generated_push_gate eol-normalization fix resolves Windows autocrlf false positive (#918). Publication proven via direct matrix/emit_receipt → commit comment (06d9b607); unproven: real `git push` emission (scheduled after #919 merges). Checks API requires auth (403 user-token); commit comments are channel.
- **tests/test_commit_lint fixture hygiene (PR #919, 2026-10-07, SHIPPED):** Fixture isolation scanner + tripwire for all git config writes (core.hooksPath guard); power_selftest hooks-path-sane check added.
- **PR #922 pr_footprint_gate — SHIPPED (2026-10-07):** tools/pr_footprint_gate.py + ci.yml job `pr-footprint-gate` is now REQUIRED branch-protection check on main: fails >100 files / >5000 deletions without a big-change label, or fixture-style commit subjects.
- **PR #923 receipts spool/flush — SHIPPED (2026-10-07):** receipts spooled at pre-push under state/receipts/spool and flushed once the sha exists: hook start + pr_sweep.
- **PR #925 regen_all/gen_tool_index --check read-only — SHIPPED (2026-10-07):** regen_all/gen_tool_index --check are read-only and eol-normalized via git hash-object; fixes the one real local shard failure on Windows.
- **PR #926 LANE-CONTRACT section 4a — SHIPPED (2026-10-07):** ordered pre-push self-checks: footprint, pyflakes, hygiene, claudemd, regen, worktree-only push, explicit --head, EMIT=0 interim.

### In Progress

### Open / Queued

18. **Promote pr_symbol_survival_check (G13) to blocking** (false positive found 2026-10-14 in the window: nested defs; fixed in this PR; new clean window starts at merge date; promotion requires adding required check `pr-symbol-survival` to branch protection). Orchestrator does the protection change, not the lane.
19. **Extend hooks/no-polling.mjs** (QUEUED). Deny backgrounded whole-filesystem searches in lanes.
20. **tests/test_test_hygiene.py lookback off-by-one** (QUEUED). Lookback window hardcoded as 19 lines; doc claims 20. Review window semantics + fix mismatch (2026-10-07 audit carried forward).
21. **pyflakes ratchet G15 gate wiring** (OPEN/IN PROGRESS). A lane is wiring pyflakes to pre-push gate list; fixes ~50 remaining issues found during #890 sweep; do not claim merged until PR lands.
22. **Receipt gate increments 4–6** (QUEUED). Increment 4 (agreement measurement via tools/receipt_agreement.py) gated on measurement completion; increments 6a–6c (signed ledger storage, lineage codec) per conductor3 plan.
23. **AesopMergeQueue task lifecycle decision** (QUEUED, decision pending). Scheduled task `AesopMergeQueue` Status: Disabled since 2026-09-11; retire gracefully (cleanup PR) vs re-enable?
24. **Receipt keys, Mattt box** (IN PROGRESS — Matt 2026-10-14 lifted the deferral; branch chore/receipt-key-mattt-box, origin 050ea764, no PR at checkpoint). Second-box (macOS) key provisioning follows once it lands.
25. **Mid-wave defects 1-19** (see CURRENT block) — queued for fix lanes; PR-tracked items are listed under In Progress.
25. **PR #921 CLOSED without merging** (branch corrupted: fixture commits; salvaged as #923).

### Blocked / Deferred

- **WSL distro install** (APPROVED by Matt 2026-10-14, firmware-blocked on this box). Virtualization is disabled in firmware; Virtual Machine Platform enable was attempted. Needs a BIOS change and reboot. #872's detection fix correctly reports "no distro" until then.
- **Receipt keys:** no longer deferred (Matt 2026-10-14). Mattt-box provisioning in progress; see item 24.
- **Merchant feed hosting choice** (BLOCKED — Matt). Where tannery-merchant-feed.tsv (claude-scripts #31) gets served from.

**Merge actor session-independence — DONE (2026-10-06, PR #871):** GitHub native auto-merge (armed per-PR at open via `gh pr merge <n> --auto --squash`) is the session-independent merge actor; `AesopMergeQueue` Scheduled Task is correctly `Status: Disabled` — deliberate retirement of the old label+daemon regime. Real gap found and fixed: `tools/dispatch_lint.py` unconditionally forbade `--auto`/bare `gh pr merge`, blocking the exact command LANE-CONTRACT.md requires to arm the merge actor; now `gh pr merge <n> --auto --squash` is explicitly allowed while manual merge, bare `--auto` with no PR number, and `--admin` remain forbidden. 45/45 + 223/223 regression tests green.

**Release-state note:** `v0.7.1` is tagged at `ec5ea9db` and has **no GitHub release** — that commit's CI was red (pre-existing `/api/state` bug). The tag was deliberately NOT moved, since retagging a pushed release rewrites published history. `v0.7.2` is published on npm (Latest, MIT license) and GitHub (Release v0.7.2 Latest). Consumer-visible release history therefore reads 0.7.0 -> 0.7.2; publishing 0.7.1 retroactively is a user decision. `v0.7.1` remains tag-only on git. Current unreleased commits: 219 since v0.7.2 tag (as of 2026-09-11, HEAD 07732210).

**Licensing:** Aesop v0.7.2 and earlier npm artifacts (v0.7.0, v0.7.1, v0.7.2) are MIT-licensed (permissive, commercial use allowed). PR #799 (merged 2026-09-11) relicensed main and HEAD to PolyForm Noncommercial 1.0.0 (research/indie OK, no commercial use). Version v0.7.0 and earlier on npm remain MIT forever.
