# STATE — Durable System Checkpoint

**What this file is:** The live durable checkpoint that Aesop itself uses during its own `/buildsystem` loop. It records the current system version, architectural decisions, known limitations, and the next milestone. This is not historical archive; it is read by the orchestrator to understand operational state.

**Current Version:** v0.9.1 (tag 5026195d, cut 2026-10-09; GitHub release published 2026-10-10; npm @matt82198/aesop 0.9.1; 40 PRs since v0.9.0). Previous: v0.9.0 (2026-10-07).

## CURRENT (2026-10-14 mid-wave checkpoint)

**Mid-wave checkpoint, round 1 audit + fix wave.** Every fact below was verified with `gh`/`git` at write time (2026-10-15 ~01:07Z, Mattt box). PR states move; re-verify before acting. Companion ledger: BUILDLOG.md section "2026-10-14 mid-wave checkpoint".

- **origin/main = `769cd103`** (#952). Latest completed main-full on main: success on `2c59d40f7d` (2026-10-15 00:59Z). main-full for `b17b899136`, `a2d98c449b`, `769cd103` was still in progress at verification. Regenerate On Main and Deploy Dashboard green on every completed head.
- **Earlier RED main fixed:** scheduled run 38538275152 (windows aggregator treated a docs-only schedule skip as failure) is closed by #944.

**Shipped today (state verified with gh):**
- aesop: #943 (state_md_verifier claim precision), #944 (windows aggregator docs-only skip), #945 (orchestrator checkpoint), #946 (incident commit-body ordering), #947 (G13 nested defs, advisory), #949 (bench unit tests cannot reach the paid live API), #950 (wave preset testCmd lint, F-11), #952 (wave_loop bounded exec and multi_dispatch claim keyspace, F-03/F-04), #954 (pr-sweep retriggers CI on armed PRs with zero check runs).
- claude-scripts (`matt82198/claude-scripts`): #37 (power_selftest fixture), #38 (power_sync.py), #39 (secret_scan doc false positive), #40 (secret_scan --staged scans the index blob), #41 (fail-closed red-CI queue, main coverage, brain ahead/behind, $HOME expansion).

**In flight, open PRs (verified OPEN):**
- aesop #948 chore/incidents-2026-10-14 (head cebb5d43): incident ledger entries.
- aesop #951 fix/wave-scheduler-tracker-writeapi (head 19607fde): F-01.
- aesop #953 fix/state-db-path-resolver (head 2580d3ca): F-02, F-06.
- aesop #955 fix/watchdog-heartbeat-blocked (head 5959ccae): blocked cycles write heartbeat.
- aesop #956 fix/test-collection-and-flake (head dffdb953): misnamed TestCase module, coverage gate fails closed.
- aesop #957 fix/watchdog-halt-boxwide (head 7ce40f7f): box-wide halt sentinel, fail closed.
- aesop #932 perf/receipt-matrix-scoped and #934 chore/stats-refresh: auto-merge armed, zero check runs ever (defect 17).
- **Lanes not yet on origin (no branch at verification):** fix/docs-only-anchor-windows-gate, fix/auto-merge-verify-merged, feat/verify-receipt-fail-closed, feat/incident-class-trailer, feat/worktree-ownership-guard, fix/shard3-serve-tests.
- **Key lane:** chore/receipt-key-mattt-box is on origin at `050ea764`; no open request yet.

**Machinery on the Mattt box (verified 2026-10-14 20:07 CDT):**
- `AesopWatchdogDaemon` Status Ready, next run 20:10. Heartbeat `~/conductor3/state/.watchdog-heartbeat` mtime 20:04:36 (about 3 minutes old at check). Blocked cycles skip the heartbeat until #955 lands.
- `AesopRefinementMonitor` is not registered on this box. A legacy Disabled `\Aesop\AesopWatchdogDaemon` task remains (defect 6).
- Brain hooks changed today. `claim-binding.mjs` is wired in settings.json. `no-grep-on-main-thread.mjs` is wired. Fail-closed conformance harness `_failclosed.test.mjs` is in progress (defect 19).
- Receipts: signed receipts run on the matt8 box. Mattt box key provisioning is in progress (see above). verify-receipt fail-closed is not yet pushed.

**Audit round 1 (2026-10-14, reports in ~/conductor3/audits/2026-10-14-r1/):**
- 9 lenses done: adv-enforcement, adv-reliability, analyst, architecture, brain-tooling, guardrails, regression, security, orchestrator-drift (plus model-rewrite investigation).
- Refuters done for adv-reliability, architecture, brain-tooling, guardrails, regression. Refuters for security and adv-enforcement were in flight at checkpoint. analyst and orchestrator-drift have no refuter report on disk.
- Lens model caveat: 12 of 24 Opus dispatches were spawned as Haiku by the harness. Feedback filed. Round 2 must check each lens's real model.

**Verified defects awaiting fix (short; severities after refuter adjustment where refuted):**
1. `tools/incident_report.py --check`: docs/INCIDENTS.md drift on origin/main (latent red if the check is a CI gate).
2. `incident_report.py --regenerate --output <abs path>` crashes.
3. `incident_report.py` print mode crashes on Windows cp1252 (needs utf-8 stdout).
4. `find_all_incidents` sort order contradicts its docstring.
5. #946 classifies full commit bodies (182 rows vs 113). Review when INCIDENTS.md is regenerated.
6. `daemons/install-tasks.ps1` divergence check matches task names across folders.
7. Registered watchdog command carries a literal `$HOME` that power_selftest cannot resolve on Windows.
8. Unwired hooks: capture-open-findings.mjs, main-context-ceiling.mjs, plus one more (selftest hooks-wired:3).
9. auto-commit-brain.mjs pushes to master per save without secret-scan. Lane claim, not re-run by this checkpoint.
10. sync_repos.py fast_forward_repo non-default path runs `fetch origin <default>:<default>`. Lane claim, not re-run.
11. power_selftest whitelist strays in ~/.claude root (cleanup decision).
12. MEMORY.md index 25.5 KB, over the 24 KB warning.
13. Hook policy text suggests "sonnet" for untyped dispatch (doctrine is haiku). User wording decision.
14. Primary checkout root has untracked junk (m.html, pp.json, q.json, r2.json, rc.json, sub.json, subs.json, `$HOME/`). Left untouched (no-delete rule). Listed for Matt.
15. ~/.claude/hooks/session-end-log.mjs fails schema validation, so it silently fails at every session end.
16. no-grep-on-main-thread.mjs fires for subagents in sibling worktrees. It must detect the orchestrator main thread only.
17. PRs armed for auto-merge with zero CI runs have no retry actor (#932, #934).
18. primary-branch-guard.mjs matches substrings of the command, not the git verb or resolved repo. Also blocks text that merely contains a phrase.
19. Wave item: fail-closed conformance test for ALL hooks in ~/.claude/hooks (malformed JSON, empty stdin, missing cwd, forced throw; assert deny or exit 2).

**Receipts gap (design vs implementation).** Design: harness-constructed intent; signed write-ahead receipt per action; measured action from artifacts the agent cannot write; zombie gate that kills a lane whose measured action diverges from intent; partial receipts; key outside the agent's reach; meta-gate that blocks. Implemented: one receipt per push, spool/flush, verify-receipt (neutral on missing), claim-binding Stop hook, hooks_wired_gate (reports, does not block). Gaps: no per-action receipts; intent not bound into the receipt; no zombie-gate kill; no partial receipts; verify takes key and verifier from the PR checkout (security F-07); meta-gate does not block. Filed as incidents plus a plan lane (not yet pushed).

**Decisions (Matt 2026-10-14 and /afk rulings):**
- All hooks fail closed. A hook denial on the main thread is stop-and-report, never a detour.
- Incidents go to the incident log through the commit-trailer convention. Prose .md rules are banned.
- Receipt keys are no longer deferred (provisioning in progress on the Mattt box).
- Audit lenses stay on Opus; fix lanes stay Haiku. Every dispatch states `model` explicitly.
- Cap question resolved by /afk ruling (reversible): GitHub Actions concurrent-job cap is THE cap, monitored by idle_tick, detect_red_ci_runs and pr_sweep.
- Release: patch 0.9.2 (CI and machinery fixes only), cut once main is green. Reversible if Matt names a version.
- WSL is approved but blocked by firmware virtualization (BIOS change and reboot needed).

**Release gate status: NOT MET.** Current release v0.9.1. Done: #944, #945, #946 (see Shipped). Still open at verification: #948, #951, #953, #955, #956, #957, #932, #934, plus the unpushed lanes above. Main-full green on the release head is not yet confirmed. The release is blocked until those land.

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
