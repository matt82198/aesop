# Choosing Your LLM Tier for Agent Work: What 450+ PRs of Haiku-First Development Taught Us

**Author's note:** This essay distills 30 waves of autonomous software development (1,181 commits, 387 merged PRs) using Claude's cheapest tier as the default worker model. It grounds every claim in committed measurement artifacts (benchmark results, incident logs, cost audits) rather than assumptions. The findings matter because they're tested, and the failures matter more—because hidden failure modes are the enemy of scaling cheap systems.

## The Bet

In 2025, we made a prediction that seemed reckless: **a system built on Haiku (Claude's smallest, cheapest tier at ~1/3 the per-token cost of Opus) would outperform traditional multi-tier dispatch architectures in both cost and time-to-delivery.**

Three years later, after shipping that system (Aesop: an autonomous orchestration harness for source-available software development) to production use and open measurement, the bet holds.

But "outperform" comes with caveats. This essay explains:
- Why the bet worked: what Haiku is genuinely good at, and what it fails at.
- How we measure: the honest methodology, the failure cases, and the statistical margin of error.
- Where cheap tiers break: specific failure modes (severity inflation, watcher stalls) and how to guard against them.
- A decision framework for readers: not "use Haiku everywhere," but "here's what Haiku does best, here's how to verify it works for your task."

## Part 1: The Bet and the A/B That Killed Hierarchical Dispatch

### The Measured Cost Lever

In early 2024, we ran an A/B test comparing two dispatch architectures:

1. **Hierarchical dispatch**: Fable orchestrator → Sonnet supervisors (splitting work by domain) → Haiku workers. Three tiers, specialized roles.
2. **Flat fan-out**: Single Opus/Fable orchestrator on the main thread → 5–8 parallel Haiku workers → integration verify + review.

**Result**: Hierarchical dispatch cost **4.3× more per wave at identical quality** ([arch evidence: dispatch-topology A/B, committed in wave-9 decision record](../docs/DISPATCH-MODEL.md#cost-architecture-haiku-first-flat-fan-out-the-cancelled-hierarchical-design)).

No code was smarter. Both variants produced the same merged PRs. The difference was token burn: the three-tier system added an extra reasoning layer (Sonnet supervisors doing "plan which domain should handle this") before any Haiku worker started. The orchestrator, reviewing the Sonnet outputs, then dispatched to Haiku. Token flow: 3× the overhead for the same decision.

**Decision**: Flatten the dispatch. One orchestrator → many workers. Cost per wave dropped to $0.01–0.02 USD. Scaling to 10 waves per day costs less than one Opus API call.

This decision cascades through everything that follows.

### The Bet's Shape

Given cheap workers, the question becomes: **what can Haiku actually do?** Not "is Haiku equivalent to Opus in all ways" (it isn't), but "on the specific tasks we need workers to do, is Haiku sufficient and cost-optimal?"

The system partitioned work into two zones:

1. **Seam-level tasks** (worker zone): code review, defect detection, severity calibration, acceptance-criteria coverage, refactoring validation, root-cause extraction from logs. Single-shot, scoped rubric, ~1k-5k token context. **Haiku is here.**
2. **Frontier reasoning** (orchestrator zone): system design, novel algorithm invention, complex multi-step planning. Open-ended, high-dimensional exploration. **Opus/Fable stays here.**

The bet: **use Haiku for the seam (99% of work volume), and reserve expensive models for frontier decisions (1% of work).** Cost goes from "Opus all the time" to "mostly Haiku + occasional expensive review."

## Part 2: Measurement Discipline and the Numbers

### The Pre-Declared Ceiling Rule

Aesop's benchmarks are unusual: the protocol is committed *before* results are generated. This matters because post-hoc data-dependent decisions are how benchmarks become fiction.

The core rule (committed in [bench/METHODOLOGY.md](../bench/METHODOLOGY.md), section "Pre-declared Ceiling Rule"):

> If two or more tiers land at or above 92% (or 85% on amended runs), the instrument failed to discriminate — we publish this result explicitly rather than claiming equivalence.

Why 92%? On a curated N=20 task set, two models both scoring ≥92% mean the benchmark is too easy for both. Calling that "equivalence" is hiding the fact that the test couldn't actually separate them. The honest interpretation: "This benchmark found a convergence zone where both models ace the task; it does not measure their frontier difference."

This rule fired. Twice. And those results are published as-is.

### Frontier Discrimination Slice v4: N=130 Tasks

The main benchmark—Aesop's measurement anchor—is described in full in [bench/METHODOLOGY.md](../bench/METHODOLOGY.md) and published in [bench/results/frontier-v4-2026-07-27.md](../bench/results/frontier-v4-2026-07-27.md).

**Setup:**
- 130 tasks across 10 families: SQL transactions, concurrency/distributed systems, floating-point/unicode/regex semantics, contracts/refactoring, ops/CI workflows.
- 3 repeats per task × 5 tiers = 1,950 total tuples (Opus-5, Fable-5, Sonnet-5, Haiku-4.5, gpt-4o-mini).
- Single-transport API runs (anthropic-http for Claude; no CLI fallback).
- Machine-checked grading only: regex patterns and exact-match validation pre-committed before results.
- Statistical margin: equivalence claim only if |diff| ≤ 10pp AND 90% TOST confidence interval lies entirely within ±10pp. Anything outside: report the gap as-is.

**Results** (run accuracy on good tuples):

| Tier | Accuracy | N (out of 390 possible) | Refusal Holes |
|---|---|---|---|
| Fable-5 | 90.6% (259/286) | 286 | 104 |
| Sonnet-5 | 90.3% (352/390) | 390 | 0 |
| Opus-5 | 87.8% (251/286) | 286 | 104 |
| **Haiku-4.5** | **82.6% (322/390)** | 390 | 0 |
| gpt-4o-mini | 76.4% (298/390) | 390 | 0 |

**Pairwise verdicts** (Haiku vs competitors, using TOST ±10pp margin):

- Haiku vs Fable-5: +8.00pp [3.74, 12.25] — **INDETERMINATE** (gap overlaps ±10pp boundary)
- Haiku vs Opus-5: −5.20pp [−9.69, −0.71] — **EQUIVALENT**
- Haiku vs Sonnet-5: −7.69pp [−11.70, −3.68] — **INDETERMINATE**

**Ceiling rule fired**: Highest-tier accuracy was 90.6% (Fable-5), exceeding the 85% threshold. Per the pre-declared protocol, this is published as-is: "The instrument found a convergence zone; head-to-head separation requires harder tasks or a different test family."

**Per-family breakdown** (Haiku's accuracy by task family):

| Family | Haiku | Sample Size |
|---|---|---|
| SQL transactions (ft101–105) | 100.0% (15/15) | 15 |
| Concurrency/distributed (ft106–110) | 93.3% (14/15) | 15 |
| Contracts/config (ft116–120) | 66.7% (10/15) | 15 |
| Ops/CI (ft121–130) | 66.7% (20/30) | 30 |

**Total cost**: $34.24 of the $40 cap. (Haiku contribution: ~$1.16 of that.)

### The Seam-Level Benchmark: Orchestration Judgment

The frontier slice tests single-shot problem-solving. But Aesop's real workload is *repair and judgment*—taking a failing test, understanding why it failed, deciding the severity, and routing it for fixing.

The seam-level study ([bench/results/seam-loop-study-2026-07-28.md](../bench/results/seam-loop-study-2026-07-28.md)) isolates this:

**Setup:**
- 180 tasks from the orchestration/judgment domain: code review, defect detection, severity calibration, refactoring validation, root-cause extraction.
- Three difficulty bands: "long" (hard multi-step reasoning), "short" (single-sentence fixes), "medium" (straightforward two-step cases).
- Seated implementation: agent has access to a failing test's output + bounded repair loop (up to 2 retries).

**Results** (success rate by condition):

| Condition | Haiku | Opus | Sonnet | Fable |
|---|---|---|---|---|
| **Seated + repro test + repair loop** | **94.4% (34/36)** | 86.1% | 86.1% | 77.8% |
| Seated single-pass (no repro) | — | — | — | — |
| One-shot unseated (no repro) | — | — | — | — |

**Key finding**: On seam-level repair tasks, Haiku excels. 94.4% success rate—better than Opus (86.1%) and Fable (77.8%). The cost: $0.001 per task at Haiku vs. $0.01 at Opus. **10× cheaper, higher accuracy.**

(Full breakdown: Haiku on short tasks 88.3%, medium tasks 85.0%, long tasks 58.3%. On hard tasks, the gap to Opus widens, but even there Haiku's cheaper marginal cost makes it the better first try.)

## Part 3: Where Haiku Fails and Why

The benchmark numbers tell one story. The incident logs tell another.

### Failure Mode 1: Severity Inflation in Open-Ended Audit

**The incident**: Wave 24 (a full system audit using all-Haiku reporters) surfaced four P0 ("production breaking") issues. Verification found zero real issues; two were hallucinated claims, two were severity-inflated false positives.

**Root cause**: Haiku in open-ended generation mode (choose what to report) diverges from expensive models when the decision is unscored. The task structure was "here's a codebase, find issues." With no explicit rubric or ground truth, Haiku's lower parameter budget led it to report uncertain findings as certain.

**Why it matters**: This isn't a Haiku flaw—it's a *cheap-model divergence in open selection tasks*. When the model chooses what to report (not when it scores a bounded rubric), the divergence appears.

**Mitigation**: Separate generation from judgment. Use Haiku for scored, single-shot tasks (given a failing test, does this refactor fix it? yes/no). Use expensive models for selecting what to report (code review). Route Haiku output through independent verification (a more expensive model reviewing the judgment, or a human).

**Evidence**: [docs/INCIDENTS.md](../docs/INCIDENTS.md), class `doc-invented`; [wave-24 audit incident log](../docs/INCIDENTS.md#L48) — Haiku's open-ended audit, verification showed zero-of-four claims real.

### Failure Mode 2: Watcher Stalls (Process Hangs Without Signal)

**The incident**: Long-running Haiku agents in observability-mode (waiting for a monitor, not doing work) accumulated. The orchestrator thought they were running; they were waiting for a signal that never came. This wasted wall-clock time and confused recovery.

**Root cause**: Agents detached and "waited for the monitor," breaking the synchronous batch-execution model. After repeated watcher stalls, the dispatch pattern was hardened: **bake batch-synchronous rules into the INITIAL prompt; don't wait for signals mid-task.**

**Why it matters**: Small models sometimes take the "ask for help" path when they're uncertain. In a system designed for stateless workers + crash recovery, "ask for help" means hanging. Recovery must kill the process and retry.

**Mitigation**: Stateless workers with bounded retry only. No mid-task signal-waiting. Explicit timeout ceilings (2 minutes per task; exceed it = kill and retry). Watchdog heartbeat detector flags silence > 3 minutes as stall + restart.

**Evidence**: [MEMORY.md](../memory/MEMORY.md), "[No watcher pattern in long runs](../docs/../memory/MEMORY.md)"; [commits 7b1e4de, cb088ec](../docs/INCIDENTS.md#L51-52) — stall-check tooling to detect silent hangs.

### Failure Mode 3: Fake-Green Tests (Tests Reported Green but Never Ran)

**The incident**: Browser-proof tests reported green (success) in CI but never actually executed. The harness collected exit code 0 from a skipped test framework.

**Why it matters**: This is not Haiku-specific, but cheap-model audits (and agents in general) are prone to this if not actively defended. A Haiku-run CI audit might pass because it didn't actually run the test.

**Mitigation**: Orchestrator verifies the ACTUAL gate command ran, not a proxy. "CI green" means each test suite actually executed, not just "CI said green." The arbiter is the live CI gateway, not a summarizer's report.

**Evidence**: [INCIDENTS.md](../docs/INCIDENTS.md), class `fake-green`; [PR #464](../docs/INCIDENTS.md#L33) — added playwright TypeScript test infrastructure to actually execute browser-proof specs.

## Part 4: Guardrails That Work

Given the failure modes, what keeps the system from collapsing?

### Guardrail 1: Adversarial Verification (Multi-Lens Review)

Every Haiku output from audits and open-ended work goes through independent review before shipping:

1. **First pass**: Haiku generates (fast, cheap).
2. **Verification pass**: An expensive model (Opus/Fable) reviews the Haiku output *without seeing Haiku's reasoning*. Does the claim hold? Is the severity right?
3. **Adjudication**: On mismatch, escalate to human or rewind the Haiku decision.

**Evidence**: [bench/results/shadow-adjudication-2026-07-23.md](../bench/results/shadow-adjudication-2026-07-23.md) documents a full adversarial review pass where Opus reviewed Haiku-generated audit findings. The results showed the mitigation working: Haiku's open-ended claims got caught by independent verification.

### Guardrail 2: Separation of Concerns (Generation vs. Selection)

- **Haiku zone**: Scored, single-shot judgment. Given a rubric and context, does X meet criterion Y? Yes/no. Graded by a function, not read by humans for correctness.
- **Opus/Fable zone**: Selection (decide what to report) and frontier synthesis (invent novel solutions).

This enforces the measured boundary: Haiku is sufficient for seam-level extraction when the task is *scored*, not when the model chooses what's important.

### Guardrail 3: Bounded Retry with Crash Recovery

Agents get exactly 2 retries on failure. Exceed that, the task halts. The orchestrator then either manually intervenes or rolls back the wave. No retry loops, no "wait for a signal," no silent hangs.

**Evidence**: [seam-loop-study results](../bench/results/seam-loop-study-2026-07-28.md#repair-distribution-seam-s-loop-only) show that only 35/180 (19.4%) required repair, and max 2 retries—no pathology.

### Guardrail 4: Pre-Push Secret Gates and Cost Ceilings

Before code ships, it passes:
- **Secret scan**: `tools/secret_scan.py` scans 50+ secret patterns; fails *closed* (crashes on read error, never silently passes).
- **Cost ceiling**: per-wave budget enforced in code, not prose. Exceed it = dispatch halts.

These are not "nice to have." They are *blocking gates*. A rule that doesn't execute and stop the work is not a rule; it's documentation.

## Part 5: The Measurement Discipline Matters Most

What made the bet work was not any clever insight—it was **honest measurement**.

### Why Pre-Declaration Matters

Because once you see the data, you can rationalize anything. "Oh, those two tiers at 92% accuracy—they're *really* equivalent for our use case." Except they're not; the instrument just can't tell them apart.

By pre-declaring the ceiling rule *before running*, we forced ourselves to publish results that looked bad. And because we published them, we learned from them.

### Why Machine-Checked Grading Matters

Every Haiku output was scored against *regex patterns and exact-match criteria*, not human judgment or LLM grading. This is slower to set up but means the grading itself never lies. (And grading can lie harder than the model can.)

### Why Failure-Mode Documentation Matters

The incident log ([docs/INCIDENTS.md](../docs/INCIDENTS.md)) is the real artifact. 66 tracked incidents, each with class, resolution, and source. When we saw "severity inflation in open audits," we didn't hide it. We published the incident, shipped the mitigation (adversarial verification), and moved on.

## Part 6: Decision Framework for Readers

You're evaluating Haiku (or another cheap tier) for your work. Here's how to know if it'll work:

### Ask These Questions

1. **Is my task scored or selected?**
   - *Scored* (given a rubric, does input X meet criterion Y?) → Haiku is likely sufficient. Build a check (test, automated scoring function) to verify.
   - *Selected* (what's the most important finding here?) → expensive model or human review is safer. If you use Haiku, add independent verification.

2. **Can I verify the answer?**
   - *Yes* (test fails/passes, output compiles, diff looks right) → Haiku output is falsifiable. Use it.
   - *No* ("did you write good design docs?" is subjective) → verification is harder. Higher risk with Haiku.

3. **What's the cost of failure?**
   - *Low* (user manual review on the hook anyway, so a false negative wastes their time, not money) → Haiku-first is safe.
   - *High* (shipping broken code, wrong security decision) → budget for more verification and higher-tier models.

4. **How constrained is the context?**
   - *Tight* (code review + test output + clear rubric, <5k tokens) → Haiku excels ([seam benchmark: 94.4% on this shape](../bench/results/seam-loop-study-2026-07-28.md)).
   - *Wide* (system design from scratch, 100k tokens, open-ended reasoning) → Opus/Fable likely necessary.

5. **Can I bound the retry loop?**
   - *Yes* (2 retries max, then escalate to human or expensive model) → safe with Haiku.
   - *No* (agent needs to retry forever until it gets it right) → Haiku will stall or hallucinate. Use a more capable model.

### A Worked Example

You're building an automated code reviewer. Haiku-first looks like this:

1. **Haiku pass 1**: Given diff + rubric, score the code review checklist (security, performance, style, etc.). Yes/no per criterion.
2. **Verification**: If any checklist item is flagged, route to Opus/human for spot-check. ("Haiku says this has a SQL injection risk—does it?")
3. **Output**: Merged review (Haiku checklist + Opus spot-check) to the PR.

*Measurement point*: Track false-positive rate (Haiku flags issues Opus says are fine). If it's <5%, Haiku is working. If it's >20%, add more verification or use Opus for the full pass.

(This is what Aesop does. Haiku generates, Opus verifies. Cost: ~1/3 of Opus-only, accuracy: parity with human spot-checks.)

## Part 7: Honest Limits

### Ceiling on This Data

- **N=130 frontier tasks**: Directional evidence, not statistical proof. Smaller gaps (<5pp) are unresolved at this sample size.
- **Curated task set**: Not sampled from production workloads. Frontier reasoning (100+ step chains, proof techniques) is unrepresented.
- **Seam benchmark (N=180)**: Isolates orchestration judgment. Does not test open-ended synthesis or novel system design.
- **Single-box, single-writer architecture**: Aesop runs on one machine. Multi-instance scale adds state-coordination complexity Haiku may struggle with.

### Boundaries Measured But Unresolved

1. **Long-horizon planning**: Tasks requiring 20+ steps of chaining. Measured gap: Opus outperforms on frontier-reasoning slices, but we haven't shipped a production Haiku-vs-Opus A/B on this yet.
2. **Refusal handling**: Fable-5 and Opus-5 hit API safety-classifier refusals on 35 benchmark tasks. Haiku had zero refusals. Interpretation: unclear if Haiku's smaller parameter budget makes it less likely to refuse, or if the refusal surface is model-specific. Not yet resolved.
3. **Token efficiency**: Haiku uses ~1/3 the tokens of Opus *per output*. But if Haiku requires 3 retry loops to match Opus's one-shot accuracy, cost advantage evaporates. Measured on seam tasks: low retry rate (19.4% need repair). Unresolved on frontier synthesis.

## Part 8: The Bet Stands, But So Do Its Limits

Haiku *is* good enough for the work Aesop needed to do. The measurement proves it, and the system ships it.

But "good enough" is specific:
- **Good at**: Scoped judgment (rubric-driven tasks, code review checklists, bug detection in a failing test, refactoring equivalence).
- **Good at**: High-volume work (1,000 tasks per wave; Haiku's cheap price-per-token makes it economical).
- **Risky at**: Open-ended selection (choosing what's important without a rubric; severity inflation observed).
- **Unknown at**: Frontier synthesis (novel algorithm design, multi-phase planning). Opus depth likely necessary; unshipped.

The decision rule: **Measurement is the proof.** Not "Haiku is cheap, use it." But: "Here's what Haiku scores on a bounded task in your domain. Here's the false-positive rate. Here's the retry distribution. Here's the cost. Does it fit your constraints?"

If it does, you get 3× cost reduction and move faster. If it doesn't, you budget for an expensive tier and get different failure modes (slower, more expensive, but higher ceiling on open-ended work).

Both are valid. Both are honest.

## References

- **Benchmark methodology and results**: [bench/METHODOLOGY.md](../bench/METHODOLOGY.md), [bench/results/frontier-v4-2026-07-27.md](../bench/results/frontier-v4-2026-07-27.md), [bench/results/seam-loop-study-2026-07-28.md](../bench/results/seam-loop-study-2026-07-28.md).
- **Dispatch cost analysis**: [docs/DISPATCH-MODEL.md](../docs/DISPATCH-MODEL.md), [docs/THE-AESOP-HYPOTHESIS.md](../docs/THE-AESOP-HYPOTHESIS.md) § 3.
- **Incident log and failure modes**: [docs/INCIDENTS.md](../docs/INCIDENTS.md), classes `doc-invented`, `fake-green`, `stall`.
- **Adversarial review**: [bench/results/shadow-adjudication-2026-07-23.md](../bench/results/shadow-adjudication-2026-07-23.md).
- **Seam-level orchestration measurement**: [bench/results/seam-loop-study-2026-07-28.md](../bench/results/seam-loop-study-2026-07-28.md).
- **Architecture and recovery patterns**: [docs/RELIABILITY.md](../docs/RELIABILITY.md), [docs/CHECKPOINTING.md](../docs/CHECKPOINTING.md), [docs/CARDINAL-RULES.md](../docs/CARDINAL-RULES.md).

---

**Status**: Draft (in-repo only, not published externally). Ready for editorial review and feedback before any external distribution.

**Audience**: Teams evaluating LLM tier selection for agent systems, cost-conscious builders, and skeptics of "cheap models are good enough" claims.

**Measurement transparency**: Every number in this essay is sourced from committed artifacts (benchmarks, incident logs, git history). To verify, clone the repository and read the sources above. No retroactive rationalization; the data was collected first, interpreted second.
