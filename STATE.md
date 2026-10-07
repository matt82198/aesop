# STATE — Durable System Checkpoint

**What this file is:** The live durable checkpoint that Aesop itself uses during its own `/buildsystem` loop. It records the current system version, architectural decisions, known limitations, and the next milestone. This is not historical archive; it is read by the orchestrator to understand operational state.

**Current Version:** v0.9.0 (release/0.9.0 cut 2026-10-06 from main f41d6c40; 103 PRs since v0.8.0). Previous: v0.8.0 (2026-09-11).

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

- **Shared bare-repo core.hooksPath pollution (FIXED):** tests/test_commit_lint fixture left core.hooksPath in bare primary repo's config; pre-push hooks silently inert for hours. Cleared manually; power_selftest hooks-path-sane check added (cardinal rule 8 enforcement); #919 adds fixture isolation scanner + tripwire.
- **Lanes bypassing pre-push via bare-repo push (FIXED):** Lanes using `git -C ~/aesop push` (primary worktree) bypassed hook enforcement. Brain hook deny-default-branch-push now denies it; LANE-CONTRACT.md worktree-only rule reinforced.
- **Checkpoint/clear hooks registered (2026-10-07):** SessionEnd auto-commits control files with secret-scan gate; PreCompact enforces staleness checks with recovery hints. Durable, on-disk rules (session-end-checkpoint.mjs, checkpoint-before-clear.mjs) now enforce state transitions.

## Next Milestone

**Wave-31+:** State-layer multi-instance lifecycle (crash-orphan recovery, liveness detection, monotonic expiry); unsupervised failure-recovery loop; frontier live-run capability; external-benchmark validation.

**NEXT STEPS (post-0.7.2, ranked):**

### Shipped (Most Recent)

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
- **tests/test_commit_lint fixture hygiene (PR #919, 2026-10-07, OPEN):** Fixture isolation scanner + tripwire for all git config writes (core.hooksPath guard). Fixes core.hooksPath pollution incident (bare-repo config left pre-push hooks silent for hours); power_selftest hooks-path-sane check added. Staged, awaiting merge (full matrix ran; #919 required for proof push).

### In Progress

### Open / Queued

10. **Promote pr_symbol_survival_check (G13) to blocking** (QUEUED). After 2026-10-14 (1 week clean runs since #865).
11. **Extend hooks/no-polling.mjs** (QUEUED). Deny backgrounded whole-filesystem searches in lanes.
12. **tests/test_test_hygiene.py lookback off-by-one** (QUEUED). Lookback window hardcoded as 19 lines; doc claims 20. Review window semantics + fix mismatch (2026-10-07 audit carried forward).
13. **AesopMergeQueue task lifecycle decision** (QUEUED, decision pending with Matt). Scheduled task `AesopMergeQueue` Status: Disabled since 2026-09-11; `aesop-queue-main` branch stale at #800. Decision: retire gracefully (cleanup PR) vs re-enable? GitHub native auto-merge (PR #871) is the active merge actor; old queue is dormant.
14. **integrate/batch-* minting process guard** (QUEUED). Lane-local process that minted `integrate/batch-20261006-{2052,2158}` was never pinned to dispatch identity. Guard implemented in #899 prevents recurrence; audit for prior orphans recommended.
15. **Literal 1234567890 heartbeat writer** (QUEUED). Ephemeral test-fixture heartbeat writes used hardcoded timestamp never pinned to test identity. Structural fix deferred to #898 scope; isolated-env.mjs fixtures now guard against recurring artifacts (2026-10-07 audit).
16. **Receipt gate increments 4–6 storage/lineage/ledger** (QUEUED, post-#918). Increments 1–3 (emit/verify/action) shipped #854/#895/#903. Increment 4 (agreement measurement) gated on tools/receipt_agreement.py results (now exists, #911). Increments 6a–6c (signed ledger storage, lineage codec plans) in docs/plans. Second box (Mattt profile) receipt key provisioning queued.
17. **packaging-portability timeout hygiene** (QUEUED, watch). Windows shard-0 180s timeout mitigated by fixture sharing (#901). If timeout recurs, pull per-subtest duration_ms from CI log for targeted fixes.
18. **PR #919 merge gate (OPEN):** tests/test_commit_lint fixture isolation + hooksPath tripwire staged, awaiting CI closure. Unblocks proof push for real-`git-push` receipt emission.

### Blocked / Deferred

- **FLY_API_TOKEN** (BLOCKED — Matt). Needed for psinasty deploy.
- **Cloudflare zone for dynastywrapped.com** (BLOCKED — Matt). GoDaddy NS cutover + `cloudflared tunnel login`, then signal-hub `deploy/install_tunnel.ps1`.
- **WSL distro install decision** (BLOCKED — Matt). `wsl --install -d Ubuntu` or equivalent; #872's detection fix now correctly reports "no distro" either way.
- **Merchant feed hosting choice** (BLOCKED — Matt). Where tannery-merchant-feed.tsv (claude-scripts #31) gets served from.

**Merge actor session-independence — DONE (2026-10-06, PR #871):** GitHub native auto-merge (armed per-PR at open via `gh pr merge <n> --auto --squash`) is the session-independent merge actor; `AesopMergeQueue` Scheduled Task is correctly `Status: Disabled` — deliberate retirement of the old label+daemon regime. Real gap found and fixed: `tools/dispatch_lint.py` unconditionally forbade `--auto`/bare `gh pr merge`, blocking the exact command LANE-CONTRACT.md requires to arm the merge actor; now `gh pr merge <n> --auto --squash` is explicitly allowed while manual merge, bare `--auto` with no PR number, and `--admin` remain forbidden. 45/45 + 223/223 regression tests green.

**Release-state note:** `v0.7.1` is tagged at `ec5ea9db` and has **no GitHub release** — that commit's CI was red (pre-existing `/api/state` bug). The tag was deliberately NOT moved, since retagging a pushed release rewrites published history. `v0.7.2` is published on npm (Latest, MIT license) and GitHub (Release v0.7.2 Latest). Consumer-visible release history therefore reads 0.7.0 -> 0.7.2; publishing 0.7.1 retroactively is a user decision. `v0.7.1` remains tag-only on git. Current unreleased commits: 219 since v0.7.2 tag (as of 2026-09-11, HEAD 07732210).

**Licensing:** Aesop v0.7.2 and earlier npm artifacts (v0.7.0, v0.7.1, v0.7.2) are MIT-licensed (permissive, commercial use allowed). PR #799 (merged 2026-09-11) relicensed main and HEAD to PolyForm Noncommercial 1.0.0 (research/indie OK, no commercial use). Version v0.7.0 and earlier on npm remain MIT forever.
