# Benchmark Results Index

This index points to the canonical runs from the Aesop orchestration benchmark. All results are committed to the repository and verifiable.

## Canonical Runs

1. **[2026-07-17: Judgment Suite v3 (Haiku, Sonnet, Opus)](./results/2026-07-17-judgment-v3-haiku-sonnet-opus.md)**
   - 39 curated judgment tasks (code review, severity calibration, root-cause analysis, refactor equivalence, security spots)
   - Haiku 39/39, Sonnet 39/39, Opus 38/39 — ceiling rule trips, maps sufficiency floor not tier equivalence
   - Seam-level task coverage; not intended for frontier reasoning or long-horizon planning

2. **[2026-07-26: Judgment v3 Ceiling Addendum](./results/2026-07-26-judgment-v3-ceiling-addendum.md)**
   - Analysis of ceiling rule trip when Haiku and Sonnet both achieve 39/39
   - Pre-declared rule explanation: why the instrument failed to discriminate and what it means
   - Rationale for benchmark design and boundary conditions

3. **[2026-07-28: Seam-Loop Study (Checkpoint Recovery + Repair Loops)](./results/seam-loop-study-2026-07-28.md)**
   - Crash-only orchestration recovery fidelity (checkpoint accuracy + repair-loop lift)
   - Wall-clock latency profiles and agent behavior during recovery
   - 122/180 (checkpoint alone) → 139/180 (with repair loop) on hard tasks, +20pp improvement
   - Validates crash-only architecture assumption: "restart IS recovery"

## Methodology

For detailed protocol, amendments, and pre-registration disclosures, see [bench/METHODOLOGY.md](./METHODOLOGY.md).

All results are:
- **Pre-registered:** Protocol and success criteria committed before data collection
- **Honest:** Amendments and ceiling-rule trips are disclosed; results are reported as-is, including unfavorable outcomes
- **Reproducible:** Ground-truth patterns and test suites are committed; anyone can re-verify by cloning the repo
