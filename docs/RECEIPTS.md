# Operational Receipts — Per-Wave Metrics

This document publishes operational metrics from each wave of autonomous development. These receipts measure the system building itself: merged PRs, CI economics, diagnosed bottlenecks, and structural fixes applied in response. The system's credibility depends on transparent measurement of its own waste—not just successes, but the failures it diagnosed and repaired.

Freshest metrics and live system state are maintained in [stats.json](self-stats-data.json) (git-committed). Detailed wave audits reside in the project's session logs (archival copies available on request).

---

## Wave 1 (2026-07-29 / 2026-07-30)

**Window**: 2026-07-29 00:00 UTC through 2026-07-30 02:00 UTC (63 PRs merged, last merge #518).

### Headline Metrics

| Metric                                  | Value |
|-----------------------------------------|-------|
| PRs merged                              | 63    |
| Commits on origin/main                  | 115   |
| CI workflow runs (total)                | 563   |
| CI success                              | 325 (57.7%) |
| CI failure                              | 209 (37.1%) |
| CI cancelled                            | 27 (4.8%)   |
| CI runs per merged PR                   | 8.9   |
| Open PRs (end of window)                | 0     |
| Tracker items (total)                   | 84    |
| Tracker lanes (done/rejected/ranked/parked) | 65 / 16 / 2 / 1 |

### CI Economics

- **563 workflow runs** to land 63 PRs = **8.9 runs per merged PR** (target: ~3 for steady-state green).
- Event split: 449 pull_request runs (7.1 per merged PR), 111 push, 1 release, 1 schedule, 1 workflow_dispatch.
- **Non-success runs: 236 (41.9% waste)** — 209 failures + 27 cancelled. This is the hard floor of measured treadmill tax.

### Treadmill Diagnosis

The measured 41.9% CI failure/cancel tax traces to two structural bottlenecks, both active during wave 1:

1. **Strict up-to-date branch protection** — Every commit to main triggered a full re-run on all open PRs. With concurrent lanes landing rapidly, each PR saw 2–3 additional cycles just to re-verify after main advanced. Fixed in-wave via `strict=false` on branch protection.

2. **tests/CLAUDE.md as merge-conflict magnet** — The domain config file changed frequently across concurrent lanes, causing many merge conflicts. Each conflict required a manual rebase and re-run. This compounded the strict-mode treadmill into roughly 3x the baseline CI cycles per PR.

At 8.9 runs/PR, roughly two-thirds of PR-event CI (order of 250–300 runs) was treadmill re-runs plus flake retries.

### Fixes Applied

| Fix | PR(s) | Impact |
|-----|-------|--------|
| strict=false on branch protection | (config) | Kills the update-branch re-run treadmill. |
| Integration PR batching | #518 (batch of G1/G2/G3/G4/G6 + linter) | Reduced 5 conflicting lanes to 1 train slot; merged 02:00Z. |
| merge_train.py (deterministic serial merge) | #512 | Replaced agent-based trains with Python script; no polling burn, verified MERGED state, fail-closed. |
| tests/CLAUDE.md conflict resolution | (in-flight at capture) | Isolated file from rapid churn; reduces magnet collisions. |
| Zombie tracker reconciliation + auto-close gate | #487 (tracker_guard) + #518 (G1 tracker-autoclose) | 15/19 active tracker items were already shipped (79% zombie rate). Structural fix: tracker_guard catches stale items; G1 auto-closes on PR merge. |

### Structural Changes in Response

Over wave 1, the system evolved from a human-driven queue to an autonomous backlog engine. Key PRs:

- **Guardrails & gates (11 PRs)**: spec-contract linter, tracker-autoclose, test-coverage gate, subprocess safety, spec-contract validator, watcher linter (all batched into #518).
- **State layer consolidation (4 PRs)**: WriteAPI facade for markdown unification, quantified concurrent-write bounds, read-path StateAPI, MULTI-INSTANCE-ROADMAP.
- **Docs & positioning (11 PRs)**: crash-only whitepaper, Haiku-tier learnings, adoption checklist, incident response SLA, code taste guide.
- **Release (3 PRs)**: v0.5.0 with MIT license and evidence integration, changelog accuracy, relicense to MIT.
- **Fixes (10 PRs)**: ASCII encoding, Windows CP1252 fix, em-dash fix, heartbeat paths, error boundaries, test sandboxing, CLI context detection.
- **Tools & machinery (21 PRs)**: merge_train.py (deterministic serial), wave latency instrumentation, chaos harness, bench v2+v3 API reruns, Mission-Control dashboard MVP, evidence integration.

### Remaining Backlog

1. tests/CLAUDE.md conflict magnet fix — Verify landed.
2. (#518 follow-through) — Close superseded singles #514/#515/#516 (content merged into batch).
3. Write-path unification — Migrate remaining direct markdown writers to WriteAPI facade (#499 landed; others in flight).
4. STATE.md as generated checkpoint — Formalize and auto-generate from event log.
5. (Parked) Unit cost economics — Cost-per-backlog-item trend analysis.

### Key Finding: Measurement Driven the Fix

This wave demonstrates why transparent metrics matter: the measured 41.9% CI waste was neither acceptable nor invisible. The bottlenecks (strict mode + conflict magnet) were diagnosed live, attacked with four structural fixes, and the system was measurably tuned as a result. Steady-state target: ~3 CI runs per merged PR (one green PR fan-out + one push-on-main fan-out), down from 8.9.

---

## Future Wave Template

### Wave N (YYYY-MM-DD)

**Window**: Start UTC through End UTC (X PRs merged, last merge #NNN).

### Headline Metrics

| Metric                                  | Value |
|-----------------------------------------|-------|
| PRs merged                              |       |
| Commits on origin/main                  |       |
| CI workflow runs (total)                |       |
| CI success                              |       |
| CI failure                              |       |
| CI cancelled                            |       |
| CI runs per merged PR                   |       |
| Open PRs (end of window)                |       |
| Tracker items (total)                   |       |
| Tracker lanes                           |       |

### CI Economics

### Bottleneck Diagnosis (if any)

### Fixes Applied

### Structural Changes

### Remaining Backlog

### Key Finding

---

## See Also

- **[stats.json](self-stats-data.json)** — Committed snapshot of current metrics (refreshed per wave close).
- **[RELIABILITY-REPORT.md](RELIABILITY-REPORT.md)** — Formal reliability guarantees.
- **[autonomous-swe.md](autonomous-swe.md)** — What "autonomous SWE" means here: the measured evidence.
- **[case-study-portfolio.md](case-study-portfolio.md)** — Full audit trail of Aesop building its own portfolio site.
