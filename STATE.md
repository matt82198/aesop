# STATE — Durable System Checkpoint

**What this file is:** The live durable checkpoint that Aesop itself uses during its own `/buildsystem` loop. It records the current system version, architectural decisions, known limitations, and the next milestone. This is not historical archive; it is read by the orchestrator to understand operational state.

**Current Version:** v0.8.0 (tagged + released 2026-09-11; npm latest 0.8.0). HEAD: 4837ae60 (2026-10-05); 73 commits since v0.8.0.

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

## Next Milestone

**Wave-31+:** State-layer multi-instance lifecycle (crash-orphan recovery, liveness detection, monotonic expiry); unsupervised failure-recovery loop; frontier live-run capability; external-benchmark validation.

**NEXT STEPS (post-0.7.2, ranked):**

1. **Flaky `test_openai_transport_redirect` characterization** (DONE). Root cause identified and fixed in PR #808. Flaky test no longer reproduces under ci_shard_runner conditions.

2. **Trigger-layer selftest check in power_selftest** (GUARDRAIL #1). Verify scheduled-task execution path during POWER-SELFTEST (fail-closed if conductor3 not cloned or tasks not registered). Addresses fragility noted in Known Limitations.

3. **`test_hook_preflight` rewrite** (IN-REVIEW). Test raised module-level `unittest.SkipTest` (#667 wired it as SKIPPED); coverage gap remains. Full rewrite needed to fix `tmp_path` NameError and make test executable. Medium effort; medium impact (test-suite completeness).

4. **`test_agent_detail_roundtrip` pollution re-verify under ci_shard_runner** — DONE (this PR): re-verified stable under real shard-runner conditions (shard 3/4, round-robin, x2; alone; shard with file last after 63 preceding files) — all green, no pollution found; added `test_config_reload_isolates_state_root_per_test` regression guard (falsifiability-checked: fails if setUp's `config.reload()` is removed or made ineffective).

5. **STATE.md freshness gate** (DONE). Gate implemented and shipped in PR #809; detects stale checkpoints by comparing Current Version claim vs. HEAD commit count. Baseline freshness checkpoint set 2026-10-05.

6. **Portability path scan (box-restore / trigger-layer absolutization)** (REFACTOR). Ensure all scripts invoked by scheduled tasks use absolute paths (AESOP_HOME or durable ~/scripts location). Validates guardrail proposal from refinesystem R1. Medium effort; medium impact (multi-box readiness).
   Evidence from 2026-09-10 half-restore incident: settings hooks pointed at wrong profile, scheduled-task StartBoundary in past prevented first run, packed-refs/pack loss on restore. Guardrail tracked in PR #793.

7. **Dead-baseline liveness check** (GUARDRAIL #3). Verify that unused test baselines (e.g., .encoding-baseline.json if no encoding tests) do not accumulate. Proposed in refinesystem R1 (lens6, deferred). Low effort; low impact (hygiene).

8. **Stats-refresh PR jam** (RESOLVED). PR #781 (keeper stats) merged. Portfolio stats pipeline validated (2026-10-05). v0.8.0 released with freshness gate active; board catch-up lane started.

9. **Merge actor must not depend on a session** (IN-PROGRESS). Lanes arm native auto-merge at PR open; AesopMergeQueue task disabled. 37-PR board catch-up in progress (2026-10-05). All PRs must merge via GitHub native auto-merge, never by session daemon or manual merge.

10. **Hard checkpoint+clear enforcement hooks** (QUEUED). Implement pre-push gates to verify STATE.md/BUILDLOG.md are checkpointed before context clears. Enforce single-writer discipline on control files. Matt 2026-10-05 directive.

**Release-state note:** `v0.7.1` is tagged at `ec5ea9db` and has **no GitHub release** — that commit's CI was red (pre-existing `/api/state` bug). The tag was deliberately NOT moved, since retagging a pushed release rewrites published history. `v0.7.2` is published on npm (Latest, MIT license) and GitHub (Release v0.7.2 Latest). Consumer-visible release history therefore reads 0.7.0 -> 0.7.2; publishing 0.7.1 retroactively is a user decision. `v0.7.1` remains tag-only on git. Current unreleased commits: 219 since v0.7.2 tag (as of 2026-09-11, HEAD 07732210).

**Licensing:** Aesop v0.7.2 and earlier npm artifacts (v0.7.0, v0.7.1, v0.7.2) are MIT-licensed (permissive, commercial use allowed). PR #799 (merged 2026-09-11) relicensed main and HEAD to PolyForm Noncommercial 1.0.0 (research/indie OK, no commercial use). Version v0.7.0 and earlier on npm remain MIT forever.
