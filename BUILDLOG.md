# BUILDLOG — aesop 0.4.0 cycle

Append-only checkpoint snapshots for each wave.

## 0.4.0 Cycle Checkpoint (2026-07-25)

**Summary**: 0.4.0 staged on main@cf7fdbb; version + CHANGELOG prepared; publish user-gated.

**HS-0 Refinement Hardening** — Preliminary audit + schema/seam fixes (PRs #371–#372)
- Round-1: 6 lenses (analyst + adversarial + audit-delta); 19 findings categorized
- Round-2: 12 lenses; IPv6/DNS SSRF hardening, worker-seat redaction depth, bench stability, docs edits

**HS-1 Unified Two-Seat Config** (2 audit rounds, PR #378)
- New `seats` config block swaps worker AND orchestrator models from single config
- Legacy flat config no-op default; install-time safety
- Round-1: CLAUDE.md overflow gate, api_key_env allowlist, DNS resolution blocking, is_local loopback pin
- Round-2: promotion parity, node_id uniqueness, FakeOrchestratorBackend canning

**HS-2 Live Orchestrator-Seat Swap + Block-Gate Hardening** (2 audit rounds, PR #379 + F4 quarantine)
- final_catch gate: model swap mid-flight without restarting fleet; crash-only degradation
- Block-gate hardening (2 rounds): JSON-boundary validation, malformed-message fail-closed, evidence injection guard
- F4 quarantine fix: discovered+fixed incident-response path leaking context on crash

**HS-3 MICROKERNEL Docs** (PR #380)
- New docs/MICROKERNEL.md: proof-of-concept model-swap seam, multi-model verification bounds, 60s quickstart

**HS-4 Release Preparation** (PR #381)
- 0.4.0 version bump + CHANGELOG (v0.4.0 ships two-seat config, IPv6/DNS hardening, driver/ scaffolding, live orchestrator swap)
- main green: cf7fdbb, all required CI passing
- CLI driver/ scaffolding completeness fix (npx aesop init now emits driver/ tree)
- Bench: frontier slice results + HS-2 swap proof (bench/results/hs2-swap-proof-2026-07-25.*)

**Items Marked DONE (tracked/wave-31-close-reconciliation)**
- 0c75681341ea: shared merge-wait helper (wave-13)
- 222bab448f40: stall-detection watchdog (wave-13)
- 0e7cc4709e42: inc 1.6 schema reconciliation (PR #357+)
- b25068117995: inc 4a seated shadow adjudication (PR #358)
- 67b20009898a: ci_merge_wait fail-closed exit codes (PR #376)
- a00c762dc95c: ci_merge_wait --expect-checks semantic fix (PR #376)
- 79e40e7fecb4: windows-shard max-parallel contention hardening (PR #376)

**Confound Found & Fixed**: item-9 context-leak on answer-leak + mechanism=refutation (inc 2.5 shadow ladder). Reconciled in inc 2.6 corpus methodology; seated A/B (inc 4a) reverified with real context, item 9 flipped true both models.

**Open Parked Items** (not blocking release)
- a16eac67f7de: ps1-syntax CI gate
- fb142031d1dc: install-tasks audit log
- d1c69aed37f9: inc 2.6 broader corpus
- f84f587573fc: driver/CLAUDE.md restructure decision
- 3f7c9a2e8b14: test_frontier_slice test-pollution (NEW)

**Next**: Wave-31 backlog continuation (WS3b failure-recovery, StateAPI burndown, frontier live-run spend gate, external benchmark slice).

### 2026-07-31 -- /highvelocity + /afk session (post-0.7.0 recovery)

*(Reconstructed: these entries were appended during the session, then lost when the working copy of
BUILDLOG.md was reverted to HEAD by a tree operation. The file was uncommitted, which is why the
appends vanished. Now committed so it cannot recur.)*

**Crash recovery.** 0.7.0 was confirmed FULLY shipped before the crash (tag pushed, GitHub release
03:37, npm @matt82198/aesop@0.7.0 live). Local main was 21 commits behind origin -- pulled. Of 12
leftover branches: 9 zombies already merged, 1 never existed, 2 genuinely ahead. A recon agent
reported the opposite (claimed no tag, no release, npm at 0.6.0) because its bash was broken and it
inferred from a stale STATE.md instead of measuring -- direct measurement overrode it.

**INCIDENT -- toolchain.** usr/bin/bash.exe and curl.exe were deleted from Git for Windows (369
sibling files intact; EDR/AV quarantine hypothesis; SECOND such event). Consequences: Bash tool dead,
both daemons dead (~10h), and agents silently degrading to inference. RESOLVED: usr/bin/sh.exe
survived and IS bash 5.3.9, so copying it back restored the binary exactly (needed elevation).
Verified after: Bash works; AesopRefinementMonitor task returns 0 (was 1 every run); heartbeats carry
real daemon timestamps; power_selftest beats:ok.

**Fake-green gates found and fixed (2).** tools/state_md_verifier.py reported contradiction_count 0
and exit 0 on a STATE.md claiming v0.5.0 while the repo was tagged v0.7.0 -- it had NO version-claim
pattern and treated zero-claims-extracted as success. tools/verify_test_suite_count.py reported zero
instead of erroring when it could not read git. Both now fail closed with regression tests.

**Critical-path blocker.** tools/encoding_lint.py runs in the pre-push hook and scans the WHOLE repo,
not just changed files. main carried 62 pre-existing violations, so every push touching any .py file
was rejected -- nine finished branches stalled for hours. Cleared in PR #636 (62 mechanical
encoding='utf-8' additions, zero unrelated changes).

**Test-hygiene defects.** A test mutated the committed fixture tests/fixtures/first-wave-report.json
on every run. Another (test_ui_demo_mode.py) never set AESOP_CONDUCTOR3_ROOT, so an in-process UI
server wrote the LIVE ~/conductor3 heartbeats with the fixture epoch 1234567890 -- masking a real
daemon outage. Both fixed with guards.

**Canonical test count reconciled**: 254 (25 Node + 13 shell + 216 Python), one script, fail-closed.

### Decisions taken autonomously (/afk decision receipts)

1. **Encoding deadlock -> fix all 62, do NOT narrow the hook.** Splitting the fix across lanes
   deadlocks (no branch can reach zero). Rejected the cheaper fix of narrowing the gate to staged
   files -- altering gate semantics is user-reserved. REVERSES IF: clearing all 62 had proved infeasible.
2. **wave_loop refactor -> two-stage plan-then-apply** after a single Haiku failed the 1206-line
   radon-F(141) job by rewriting the docstring and declaring the extraction out of scope. Result:
   CC 13 (grade C), verified independently with radon; 534 tests pass.
3. **Worktree prune deferred** -- `git worktree prune` found zero stale entries, so only bulk directory
   deletion remains, which is a hard gate.
4. **README accepted at 7.6KB against a ~6KB target** -- judged substance, not fat.
5. **PR #635 closed as stale** (conflicting stats refresh; stats.json is generated output).
6. **git-history guard built, then UN-ARMED** pending revision: it left the brain repo on an unreviewed
   feature branch and denied plain `git rebase`. Restored ~/.claude to master; branch preserved and
   since narrowed (plain rebase allowed, -i/--root/--onto denied, fail-closed on unparseable input).

**ORCHESTRATOR ERRORS LOGGED (self).**
- Told every lane "Bash is broken, use PowerShell". Subagents have NO PowerShell tool. Two lanes
  stalled citing it and two audits could not write their reports. Correct guidance: agents have Bash.
- Declared the git-history guard "blocks nothing / fake-green" after piping JSON to it via PowerShell.
  WRONG: the hook reads stdin with a 2s timeout and fails open on timeout, and PowerShell's native pipe
  never closed stdin. Re-tested with `cmd /c "node hook < file"`: it correctly denies force-push,
  --amend, stash, --no-verify, --admin. LESSON: validate the test harness against a known-good
  reference BEFORE reporting a negative result.
- Relayed a security warning calling an agent's use of `[[ALLOW-MERGE-TRAIN]]` a fabricated bypass.
  WRONG: it is a legitimate documented escape hatch and the hook's own deny message instructs agents
  to use it. Read the hook before characterising an agent's action as evasion.

**Merged this session:** #631 requirements files, #632 README rewrite (20.7KB -> 7.6KB), #633 STATE.md
to 0.7.0, #634 RED-CI docs, #636 encoding backlog. #635 closed stale. Nine more (#637-#645) queued.

### Action items from session findings (2026-07-31)

DISPATCHED NOW (verified disjoint from the 9 in-flight PRs):
- A1 `fix/dead-code-check-exit` -- dead_code_check.py exits 1 inside the passing browser-proofs job.
  Decide whether exit 1 means FINDINGS (caller is wrong) or a real crash (tool is wrong), fix that
  layer, never silence it. Owns tools/dead_code_check.py + the ui/ Python tooling module + a new test.
- A2 `fix/dashboard-cost-state-a11y` -- Cost.tsx:68-70 renders backend FAILURE as "still loading"
  (three states must be distinct: loading / loaded-empty / error). Plus SVG charts have no accessible
  name. Owns ui/web/** only.

QUEUED behind the merge train (each touches a file currently in flight):
- B1 EIGHT FAIL-OPEN GATES (hooks/pre-push-policy.sh:607-612,567-570,644-648,681-684,721-724,
  832-834,865-868) -- import_resolution, tracker_guard, claudemd_sync, metrics_gate,
  test_suite_count, test_coverage, encoding_lint, force_model_policy all return 0 when their tool
  is missing. Same defect class as the two fake-green gates fixed this session. HIGHEST remaining value.
- B2 COST TELEMETRY WIRING -- state/ledger/OUTCOMES-LEDGER.md has headers and zero rows;
  tools/wave_ledger_hook.py exists to populate it and is NEVER CALLED; cost_ceiling runs 3 checks
  against data never persisted. The cost claim is unprovable until this is wired (driver/wave_loop.py).
- B3 `_quarantine_blocked_files` is still radon D (23) in driver/wave_loop.py -- pre-existing, not a
  regression from the CC 141->13 refactor, but it is now the worst function in the file.
- B4 ui/handler.py:320-329 hard-fails 500 if ui/web/dist is unbuilt (intentional, needs documenting).
- B5 tools/verify_cost_panel.py may not be pytest-auto-discovered under its current name.

USER-GATED (not autonomous): bulk worktree deletion (68 remain) - arming the narrowed git-history
guard in settings.json - `winget upgrade Git.Git` + SentinelOne exclusion escalation (2nd deletion event).

NEXT-WAVE (ideation panel, unblocked once the train clears): CI gate-activation audit as a committed
doc, transcript traceability doc, sampled benchmark (N=15-20 real repair tasks), multi-instance
design doc, tools/lib extraction continuing the CLI-base work.

================================================================================
2026-08-01 -- RELEASE 0.7.1 + 0.7.2 (main e061f2bd, tag v0.7.2, CI green)
================================================================================

SHIPPED: 11 of 12 batched PRs via integration branch #667 (merged ec5ea9db), then #668
(/api/state fix, a87c3966), then #669 (0.7.2 version bump, e061f2bd). GitHub release v0.7.2
published and marked Latest.

SEVEN DEFECTS FOUND WHILE RELEASING -- none self-reported by the model, all caught by gates:
- stateapi ratchet: tools/status_publish.py hardcoded absolute user paths to heartbeats.
- portability gate: 9 new violation keys. tools/remote_inbox.py hardcoded one GitHub handle as
  BOTH the polled repo and the sole authorized commenter -- the tool worked for one person.
  docs/REMOTE-ACCESS.md + REMOTE-OBSERVABILITY.md carried personal home paths into public MIT docs.
- THE GATE HAD NEVER RUN: portability sat behind a failing State API lint step in the same job,
  so the job exited first. Two masked failures stacked; fixing #1 is what revealed #2.
- windows EBUSY: scaffolder child process holds a dir handle; test cleanup threw on Windows only.
- stale fixture guard compared git TAGS, but CI clones without tags -- the guard could never fire.
- verify_author (self-inflicted): resolved the repo owner before validating the comment, needing
  gh auth CI does not have. Now checks well-formedness first and FAILS CLOSED on unresolvable owner.
- ci_shard_runner counted a deliberate module-level unittest.SkipTest as an import FAILURE
  (SkipTest subclasses Exception). Only surfaced on Linux ci, which lacks pytest-timeout and so
  falls back to unittest mode.

PRODUCT BUG (not a test artifact): ui/sse.py seeds the "data" CollectorSource with {} and is
mtime-gated; serve_api_state only computed inline when the cached payload was None, and {} is not
None -- so /api/state could serve an EMPTY data section on first paint (watchdog, monitor, repos,
events, alerts, messages all missing). Pre-existing: previous main 62eb2b5a failed the identical
test. Fixed in #668.

VERIFICATION FAILURE (orchestrator's own): reported main green from a monitor using
`gh run list --commit`, which is not a real filter -- it returned an empty list and the
failure-count-over-zero-rows read as a pass. Main was still in_progress and went on to FAIL.
Same vacuous-green class the release was fixing, self-inflicted in the verification step.
Memory written: gh-run-list-commit-is-not-a-filter.

PROCESS CORRECTION: v0.7.1 was tagged immediately post-merge and main's CI then went red.
v0.7.2 was tagged only AFTER main's own CI (CI, main-full, Pages) went green on that commit.
Tag-after-green is now the sequence.

HELD BACK DELIBERATELY: PR #639 (fail-closed coverage genuinely absent from the batch -- closing
it would have silently dropped work); npm publish (burns a version permanently, user-gated);
GitHub release for v0.7.1 (its commit's CI was red).

ALSO PRODUCED: Medium draft "Determinism Is a System Property" -- filesystem-as-hub thesis,
this release used as the evidence. Artifact (private):
https://claude.ai/code/artifact/d78c0706-9dcf-4c2f-991d-e84071751441
NOT published to Medium; outward publishing stays user-gated.

## 2026-08-20 — session checkpoint (box-restore completion + shutdown)

MACHINERY RESTORED TO GREEN: POWER-SELFTEST went FAIL->OK this session. Root causes: \Aesop\
scheduled tasks existed but Disabled (heartbeats stale); matt8->Mattt hardcoded paths in
collect-signals.mjs, power_selftest.py:294, scanner_selftest.py:71 (fixed, committed, pushed —
conductor3 30335df/4c7e8f9, claude-scripts c0a63a9); AesopWatchdogDaemon registered per-user
(no admin needed), 5-min cadence. Monitor cycle 467-469 ran clean; proposal #24 items 1-4 DONE,
item 5 (charter path edits) still user-gated. ~/.claude git-checkout conversion still pending.

AESOP CODE UNTOUCHED this session (branch guard/trigger-layer-selftest, no PR; sibling #793 open).
Session work was wow-workspace: cmc-work PR #17 (keybind choice-node fix, #18 folded in) ->
upstream CMC #55 (MERGEABLE/CLEAN); BRPD fork PRs #1-#3 -> upstream BRPD #1-#3. Ledger in
~/Desktop/wow/STATE.md.

SHUTDOWN (user order): aesop processes killed + \Aesop\* scheduled tasks disabled after this
checkpoint. Re-enable via schtasks /change /enable + /power next session.

GOTCHA (memorialized in aesop-dev-box-provisioning): GCM intermittently demands an invisible
interactive prompt — git push/LFS pre-push/credential fill hang silently for minutes.
GCM_INTERACTIVE=never GIT_TERMINAL_PROMPT=0 resolves from cache; lookup flaps, retry once.
