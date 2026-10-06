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

### Shipped (Most Recent)

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
- #854: Receipt gate increments 1–3 (emit, verify, non-required Action)

### In Progress

9. **Merge actor must not depend on a session** (IN-PROGRESS). All recent PRs (#860, #863, #864, #855, #850, #866, #856, #867) merged via GitHub native auto-merge. Board catch-up serial merge train for 10-PR batches continuing. #784 (Windows skills-install hang): instrumentation lane for named-step stderr + per-spawn timeouts; conflict-marker gate lane running (no PR yet). **Residual gap CLOSED**: a PR that went RED after auto-merge was armed previously had no session-independent actor (only a live orchestrator's Monitor tool noticed) -- `tools/pr_sweep.py` (+ `tests/test_pr_sweep.py`) is now that actor, invoked every 15 min from `daemons/run-watchdog.sh`'s `run_pr_sweep_throttled()`: arms missing auto-merge, nudges BEHIND branches (capped at 2/run), and signals stuck-red (>30 min)/DIRTY PRs to the conductor signal-hub queue for pickup.

14. **CI-modes product surface** (IN-FLIGHT). Expose CI run modes and performance characteristics (dispatch vs. serial, shard allocation, cross-OS drift) as browsable UI panels. Complements cost telemetry + scheduling observability.

### Open / Queued

10. **Hard checkpoint+clear enforcement hooks** (QUEUED). Implement pre-push gates to verify STATE.md/BUILDLOG.md are checkpointed before context clears. Enforce single-writer discipline on control files. Matt 2026-10-05 directive.

13. **Receipt gate increments 4–6 — after measurement** (QUEUED). PR #854 shipped increments 1–3. Pending: storage codecs, receipt lineage, signed ledger append. Foundation for auditability + billing transparency.

16. **pyflakes unused-import sweep** (QUEUED). Detect and remove unreferenced imports in driver/, tools/, mcp/, ui/ Python. Code-hygiene improvement, no behavioral change.

### Blocked / Deferred

6. **Portability path scan (box-restore / trigger-layer absolutization)** (REFACTOR). Ensure all scripts invoked by scheduled tasks use absolute paths (AESOP_HOME or durable ~/scripts location). Validates guardrail proposal from refinesystem R1. Medium effort; medium impact (multi-box readiness).
   Evidence from 2026-09-10 half-restore incident: settings hooks pointed at wrong profile, scheduled-task StartBoundary in past prevented first run, packed-refs/pack loss on restore. Guardrail tracked in PR #793.

15. **Event bridge durable deployment** (BLOCKED). Architecture: Slack webhook integration via Cloudflared tunnel (hostname + auth). Requires Matt to allocate Cloudflare account. Enables: incident notifications, lane-status pings, cost-ceiling alerts.

17. **LANE-CONTRACT line for AESOP_ALLOW_GENERATED** (BLOCKED). Add contract statement permitting lanes to deploy generated-paths registry entries. Requires prior policy clarification (generated-paths governance + merge-driver interaction).

### Follow-ups (Parked)

- **tools/INDEX.md union-merge post-merge regenerate hook** — duplicates accumulate on clean merges due to drift. Implement post-merge hook or duplicate-tolerant check.
- **Promote pr_symbol_survival_check (G13) to blocking** — After 2026-10-14 (1 week clean runs since #865). Gate is non-blocking but stable; escalate to blocking in CI.
- **Conflict-marker gate in-flight** — Lane dispatched for `guard/conflict-marker-check` branch (test fixture in flight, no PR yet).
- **#784 Windows skills-install hang — instrumentation lane** — Named-step stderr lines + ≤20 s per-spawn timeouts to capture hang location in CI log.

**Release-state note:** `v0.7.1` is tagged at `ec5ea9db` and has **no GitHub release** — that commit's CI was red (pre-existing `/api/state` bug). The tag was deliberately NOT moved, since retagging a pushed release rewrites published history. `v0.7.2` is published on npm (Latest, MIT license) and GitHub (Release v0.7.2 Latest). Consumer-visible release history therefore reads 0.7.0 -> 0.7.2; publishing 0.7.1 retroactively is a user decision. `v0.7.1` remains tag-only on git. Current unreleased commits: 219 since v0.7.2 tag (as of 2026-09-11, HEAD 07732210).

**Licensing:** Aesop v0.7.2 and earlier npm artifacts (v0.7.0, v0.7.1, v0.7.2) are MIT-licensed (permissive, commercial use allowed). PR #799 (merged 2026-09-11) relicensed main and HEAD to PolyForm Noncommercial 1.0.0 (research/indie OK, no commercial use). Version v0.7.0 and earlier on npm remain MIT forever.
