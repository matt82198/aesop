# STATE — aesop refinement loop

## Intent
Aesop is the reference implementation of the thesis: **agent behavior is source code** —
rules, memory, hooks, and checkpoints are versioned, portable, diffable filesystem
artifacts in git, so review/versioning/inheritance/enforcement/forensics apply to how
agents work. Single-user survival hack → cross-team product.

## Locked decisions (user, 2026-07-12)
- Thesis fixed as the development goal; refinement loop prioritizes the five pillars:
  onboarding-by-clone · guardrails-in-code · behavioral PRs · forensic replay ·
  cross-machine continuity.
- Orchestrator (Fable) main-thread; subagents Haiku; TDD-first.
- **Branch-per-item**: every backlog item gets its own branch + PR cut from main
  (worktree isolation for parallel implementers; agents must NEVER git-checkout in the
  primary working tree). Mega-branches retired with PR #16.
- Domain CLAUDE.mds collapsed into root (lossless); monitor extended_signals default off.

## Standing order (user, 2026-07-12)
Rerun the refinement loop CONTINUOUSLY until tokens exhaust or gaps dry (2 consecutive
audits finding nothing new). Cycle: land wave → five-lens re-audit → dedupe → dispatch
per-item branches → merge green PRs. Never idle while agents run. On session death:
resume from this file + AUDIT-BACKLOG.md.

## Phase: `0.4.0-dev` (2026-07-25, current)
**v0.4.0 STAGED**: release branch feat/hs4-release-0.4.0 prepared; version bump + CHANGELOG staged; testing in progress.
v0.4.0 ships two-seat config (HS-1/HS-2), unified orchestrator/worker model swapping, IPv6/DNS hardening,
and complete `driver/` scaffolding. Wave-31 and subsequent work proceeds under this version. Prior phase (0.3.2-released, 2026-07-23):
v0.3.1 was shipped after a recency-lane automation incident burned the v0.3.0 tag
(unauthorized `--admin` merge of #332 + empty release at red commit d81ffe4; npm
untouched; incident logged in FLEET-OPS.log, skill-narrowing proposed, memory saved).
Windows flakes on d81ffe4/ba5ed0b were runner-contention timeouts, both green on rerun;
windows streak 4-5/5 toward required-promotion (verify then promote). Domain sweep
merged (#331 consolidated #333); worktrees pruned (42 removed; feat/w28-stall-detection
kept, frontier_slice_results.json preserved untracked in bench/results/).

**BOTH 0.3.0 GATES COMPLETE (prior release).** Gate 1: supervised codex wave shipped a real
item end-to-end (PR #325; two human corrections; four scheduler defects the live run exposed,
fixed with real-shape regression tests). Gate 2: /refinesystem loop exited CLEAN at round 4 —
~30 verified defects fixed across 4 rounds, ~10 lens claims refuted with evidence, one LIVE
incident caught by the regression canary (fixture escape; two long-lived identity polluters
eliminated, one predating the cycle). Main FULLY GREEN including windows (streak 2/5 toward
required-promotion; drift: ubuntu 100%). Live accuracy measured 32/32 (gpt-4o-mini).
RELEASE-NOTES.md finalized with the honest ledger.

## Recency pass (2026-07-22, post-release): CLEAN
5 lenses -> 3 fix lanes all merged: repo docs PR #336 (CHANGELOG MIT->PolyForm CRITICAL fix, live
stats, credibility edits), portfolio PR #33 (stats v0.3.1, timeline waves 21-30), deep-dive gist
refreshed. Repo description + release title fixed. USER-GATED residuals: delete defective v0.3.0 release
entry; "Autonomous Developer" tagline reframe. Incidents logged: recency agent --admin/release
(guards proposed), PostToolUse hook wave-trigger misfire (FLEET-OPS).

## NEXT STEPS
- Wave-31 backlog (tracked): WS3b failure-recovery + unsupervised loop, WriteAPI caller
  migration, validation-ownership consolidation, StateAPI 33-entry burndown, windows
  promotion DONE (required check since 2026-07-22, 6/6 streak; 60s-timeout flakes fixed
  at 180s windows-only, PR #337), frontier live run
  (spend-gated), external-benchmark $10 slice, windows runner-contention timeout hardening
  (csrf socket test + first-hour.test.mjs; raise timeouts or cap windows job concurrency),
  optional cleanup: delete defective v0.3.0 GitHub release entry (user call).

## Phase history (collapsed)
- `pr-open` → PR #16 opened after waves 1–2 (onboarding/policy/behavioral-PR/forensics/
  continuity scaffolding + rotate_logs, reconstitute, model-policy hook).
- `wave-3-p0-inflight` → all 8 P0 + P1/P2 audit items dispatched and landed.
- `backlog-cleared` → 26/26 items ✅, final-catch green, live gate + /power dashboard
  default (web :8770) + brain hook re-synced.
- `merged-wave4-open` → PR #16 merged (`f259c4f`); branch-per-item adopted; audit #1
  dispatched.
- `waves-25-29` → credibility & safety pillar shipped (PRs #166–#171): verified audit, kill-switch
  built + wired into dispatch + PROVEN, 2 real benchmark runs (extraction tie, judgment favored
  Haiku), reconcile primitive, cost-ceiling hardening, repro CI, docs-deadlock CI fix.
- `waves-25-to-rc1` → published @matt82198/aesop@0.1.0-rc.1 (npm dist-tag `rc`, OIDC trusted publishing);
  GitHub release v0.1.0-rc.1; relicensed to PolyForm Strict 1.0.0 (SOURCE-AVAILABLE); benchmark
  measured (Haiku 39/39 vs Opus 38/39); kill-switch proven on a real wave; state-layer primitive
  audited-clean. 5 honest open residuals: benchmark (curated→transcript-sampled + latency); cost-ceiling
  (brake→live wiring); state_store sqlite CI sharding; model-dispatch core (structural, out-of-repo);
  third-party reproduce.yml untested.

## NEXT STEPS (wave-rc.2)
Honest open residuals — tracked, not ignored:
1. **Benchmark scope (curated → real-transcript-sampled)** — Current evidence (N=39) is a curated judgment set,
   not a transcript sample from live fleet. Next: sample judgment tasks from real aesop/conductor3 fleet transcripts,
   add a latency axis (response time cost), then assert "Haiku sufficient for judgment" + latency profile.
2. **Cost-ceiling: brake → live wiring** — The ceiling.py exists and is configurable, but the dispatch loop
   does not yet query it per-turn or enforce it as a live budget-guard. Wire cost-ceiling into the dispatch
   loop so per-item/per-wave spend is bounded LIVE, not just brake-able post-facto.
3. **State_store sqlite concurrency under CI sharding** — tests/test_state_store.py's concurrent-append test
   flakes under parallel CI shards (database locked). Fix: per-shard DB isolation (separate .db per shard) or
   timeout + retry logic on OperationalError (database is locked).
4. **Model-dispatch core out-of-repo (structural)** — True model routing/agent-type selection lives in the Claude
   Code harness, not in aesop. This is a cross-product concern requiring upstream movement; tracked for visibility
   but not actionable in-repo.
5. **Third-party reproduce.yml untested** — No external user has run the reproduce.yml end-to-end from a clean clone
   yet. Post-release: solicit a user run + gather feedback on UX, missing docs, env assumptions, etc.
