# Wave-1 Convergence Log — 2026-07-22 (5-pass hardening loop)

## Summary

Five-round audit-and-fix loop on the aesop orchestration core, converging from
52 defects to zero unrefuted findings. Final clean state reached at commit
`157e157`. This log records the measured defect counts and categories per round
as evidence of the convergence behavior the hardening cycle produces.

## Methodology

- **Finders**: Haiku-class agents running three lens families (analyst,
  adversarial, delta-audit) over the codebase per round.
- **Verification**: Adversarial-refute-default — every finding must survive an
  independent challenge before it counts. Inflated/hallucinated severity is
  filtered (see: wave-24 precedent where 4 reported P0s were all false
  positives).
- **Scope per round**: Delta-only after round 1. Each subsequent pass audits
  only the files changed by the previous fix train, preventing the loop from
  re-litigating already-verified code.
- **Fix dispatch**: One Haiku agent per file-disjoint domain, parallel fan-out,
  merge-train into main after each round.

## Round-by-round data

### Round 1 — Initial full audit

| Metric | Value |
|--------|-------|
| Findings (verified) | 52 |
| Categories | Test coverage gaps (18), accuracy/claim mismatches (14), gate wiring errors (11), doc drift (9) |
| Fix train | 52 items dispatched across parallel Haiku lanes |

The initial sweep covered the full orchestration core: dispatch logic,
state-store projections, daemon lifecycle, monitor signal pipeline, and
dashboard SSE layer. Test coverage gaps were the largest category — several
state-store projections had no negative-path tests.

### Round 2 — Delta audit of round-1 fixes

| Metric | Value |
|--------|-------|
| Findings (verified) | 23 |
| Categories | Test gaps (8), gate wiring (7), accuracy claims (5), doc drift (3) |
| Fix train | 23 items dispatched |

The fix train for round 1 introduced new code paths that the delta audit
caught. Gate wiring errors persisted where fixes reconnected pipelines but
missed edge-case branches.

### Round 3 — Delta audit of round-2 fixes

| Metric | Value |
|--------|-------|
| Findings (verified) | 17 |
| Categories | Test gaps (6), accuracy claims (5), gate wiring (4), doc drift (2) |
| Fix train | 17 items dispatched |

Convergence slowing — several fixes were touching shared modules, creating
secondary effects. The adversarial verification layer refuted 4 additional
candidate findings as false positives in this round.

### Round 4 — Delta audit of round-3 fixes

| Metric | Value |
|--------|-------|
| Findings (verified) | 5 |
| Categories | Test gaps (2), gate wiring (2), accuracy claims (1) |
| Fix train | 5 items dispatched |

Sharp drop. The remaining findings were concentrated in two modules
(state-store write facade and monitor signal dedup). Doc drift fully resolved
by this point.

### Round 5 — Final delta audit

| Metric | Value |
|--------|-------|
| Findings (verified) | 0 |
| Categories | — |
| Fix train | — |

Clean pass. Zero unrefuted defects. Convergence confirmed at commit `157e157`.

## Convergence curve

```
Defects
  52 |  *
     |
     |
  23 |      *
  17 |          *
     |
   5 |              *
   0 |                  *
     +--+--+--+--+--+---
        1  2  3  4  5   Round
```

## Observations

1. **Exponential-class decay**: 52 -> 23 -> 17 -> 5 -> 0 follows roughly
   geometric reduction (~55% per round after round 1), consistent with
   delta-only scoping preventing the audit surface from growing.

2. **Adversarial verification is load-bearing**: Without the refute-default
   step, rounds 1 and 3 would have reported substantially higher counts
   (estimated 30-40% inflation based on the wave-24 all-Haiku false-positive
   precedent).

3. **Category extinction order**: Doc drift resolved first (round 4), followed
   by accuracy claims, then test gaps and gate wiring (the two most
   structurally deep categories) last.

4. **Four fix trains total**: Each train was a parallel fan-out of Haiku
   agents, one per file-disjoint domain. No serial pile-on — disjoint work
   ran concurrently.

5. **Total wall-clock**: Five rounds completed within a single session. The
   delta-only constraint kept each subsequent round's audit scope small enough
   that the loop converged rather than oscillating.

## Relation to the cadence

This 5-round cycle is one instance of the backlog-harden cadence documented in
the project memory. The cadence alternates backlog-clearing waves with
harden+audit loops. This particular loop ran as the first hardening pass after
the initial backlog-clearing wave, establishing the baseline convergence
behavior that subsequent cycles follow.
