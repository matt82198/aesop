# STATE — Durable System Checkpoint

**What this file is:** The live durable checkpoint that Aesop itself uses during its own `/buildsystem` loop. It records the current system version, architectural decisions, known limitations, and the next milestone. This is not historical archive; it is read by the orchestrator to understand operational state.

**Current Version:** v0.7.2 (tagged 2026-07-31, current HEAD e5e6e22 2026-08-18, PR #787 main). 0.7.1 was a hardening release: 12 PRs against gates that reported success without verifying anything, plus the portability work needed for the remote-access features to run outside a single machine (fleet state and remote-command identity are now configuration, not an assumed home layout). 0.7.2 adds one fix on top: `/api/state` served the collector's empty default snapshot instead of computing the section inline, so the dashboard's first paint could show an empty data section. Post-tag (2026-08-02 to 2026-08-18): 201 commits (approx. 110+ merged PRs) harden CI gates, repair scheduled workflows, wire trigger-layer recovery, add multi-instance coordination safeguards, and consolidate tools infrastructure. Key merges: #676 (cost-drawer UI), #677 (gate-runability enforcement), #661/674/679 (count-gate coverage fixes), CI-hardening bundles (#780/#772/#750/#690), and scheduled-workflow repairs (#787 main-reds fix, pre-flight).

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

1. **Flaky `test_openai_transport_redirect` characterization** (IN-REVIEW). Flaky in shard 0 (pytest mode) — failed once, passed on clean re-run, passes in isolation. High impact (blocks shard confidence); low effort (1–2 PRs to isolate root cause and fix). Highest priority for unblocking.

2. **Trigger-layer selftest check in power_selftest** (GUARDRAIL #1). Verify scheduled-task execution path during POWER-SELFTEST (fail-closed if conductor3 not cloned or tasks not registered). Addresses fragility noted in Known Limitations.

3. **`test_hook_preflight` rewrite** (IN-REVIEW). Test raised module-level `unittest.SkipTest` (#667 wired it as SKIPPED); coverage gap remains. Full rewrite needed to fix `tmp_path` NameError and make test executable. Medium effort; medium impact (test-suite completeness).

4. **`test_agent_detail_roundtrip` pollution re-verify under ci_shard_runner** (IN-REVIEW). Fix landed in #668 (`/api/state` served real data, not empty default); config.reload() wired in setUp (#667). Re-verification under shard-runner conditions needed before fully closed. Medium effort; medium impact (integration-test stability).

5. **STATE.md freshness gate** (GUARDRAIL #2). Detect stale checkpoints by parsing Current Version claim and comparing committed-at date vs. HEAD date. Gate should fail if STATE.md's claimed version significantly lags behind HEAD (e.g., >50 commits). Prevents future staleness escapes.

6. **Portability path scan (box-restore / trigger-layer absolutization)** (REFACTOR). Ensure all scripts invoked by scheduled tasks use absolute paths (AESOP_HOME or durable ~/scripts location). Validates guardrail proposal from refinesystem R1. Medium effort; medium impact (multi-box readiness).

7. **Dead-baseline liveness check** (GUARDRAIL #3). Verify that unused test baselines (e.g., .encoding-baseline.json if no encoding tests) do not accumulate. Proposed in refinesystem R1 (lens6, deferred). Low effort; low impact (hygiene).

8. **Stats-refresh PR jam** (USER DECISION). PR #774 (stats-refresh) conflicted with main; PR #781 (keeper stats) in flight. User consent needed: merge #781 + close #774, or resolve conflicts and rebase batch. ~110 unreleased PRs since v0.7.2; release-cadence decision also pending.

**Release-state note:** `v0.7.1` is tagged at `ec5ea9db` and has **no GitHub release** — that commit's CI was red (pre-existing `/api/state` bug). The tag was deliberately NOT moved, since retagging a pushed release rewrites published history. `v0.7.2` (`e061f2bd`) is the first tag in this line cut *after* main's own CI went green, and is the published Latest release. Consumer-visible release history therefore reads 0.7.0 -> 0.7.2; publishing 0.7.1 retroactively is a user decision. Current unreleased commits: 201 since v0.7.2 tag (as of 2026-08-18, HEAD e5e6e22). `npm publish` has NOT been run for either version and remains user-gated.
