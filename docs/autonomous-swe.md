# It shipped itself: the 0.1.0-rc.1 milestone, told honestly

Aesop reached its first release candidate — `0.1.0-rc.1` — by running its own
`/buildsystem` wave loop across the backlog that produced it: rank work, fan out
parallel Haiku workers on file-disjoint domains, watchdog them, verify merges,
close with an audit that seeds the next wave. This document is the honest account
of what that milestone is and is not. Aesop's entire premise is candor, so the
caveats here are load-bearing, not fine print.

## What "autonomous SWE" means here

It means something specific and narrow:

> The **fleet** autonomously runs the wave loop — audit → parallel build → verify →
> merge-train — under a **human** who sets the goals and owns the outward gates.

Concretely:

- **The fleet drives the loop.** Ranking, dispatch, worktree isolation, verification,
  and the merge train run without a human hand-writing the code in each domain.
- **A human owns the outward gates.** npm publishes, tagged releases, and git history
  rewrites are all human-approved. The fleet proposes; a person disposes at the
  repo boundary.
- **The dispatch core is out-of-repo.** The actual model calls happen inside the
  Claude Code harness, which is *not* part of this repository. What Aesop ships is
  the machinery *around* that core — orchestration rules, guardrails, durable state,
  the dashboard, and tooling.

What it is **not**: it is not an unsupervised agent, not a hosted control plane, and
not AGI. Calling it "autonomous SWE" is accurate only with those boundaries stated.
Drop them and the phrase becomes hype.

## The evidence, and the honest limit on each

The milestone's real differentiator is not that an AI wrote code — plenty of tools do
that. It is that the credibility and safety claims ship with **committed artifacts a
skeptical reader can check**, each paired with the limit the project already owns.

### 1. Haiku is good enough for the judgment work a fleet does

The harness's whole cost model depends on cheap Haiku workers being adequate for
subagent judgment, not just extraction. That was long asserted from the fleet grading
its own output — agents vouching for agents, recorded in a private memory file. Not
evidence an outsider can check.

`bench/` plus `tools/bench_runner.py` replace that with a **held-out benchmark scored
by plain Python string/regex matching — no model, no agent in the grading loop**.
Across a combined **39 judgment tasks** (spanning bug-in-diff, finding-inflation,
acceptance-criteria coverage, severity calibration, root-cause-from-trace,
refactor-equivalence, and security-spotting):

| Model  | Score | Cost (per-token) |
| ------ | ----- | ---------------- |
| Haiku  | **39/39 (100%)** | 1/5 of Opus (1/3 of Sonnet) |
| Sonnet | 39/39 (100%) | — |
| Opus   | 38/39 (97%)  | baseline |

On the single task any model missed — a severity call — it was *Opus* that erred, not
Haiku. Full dated runs: [`bench/results/`](../bench/results/).

**Honest limits (the project's own words):**

- **Curated, not sampled.** The tasks were constructed by reasoning about fleet work,
  not drawn from real fleet transcripts. Selection bias remains.
- **N=39 is bigger, not statistically large.** Treat this as directional evidence for
  this workload, not a universal law.
- **It measures a floor, not the frontier.** The benchmark found *no* task where Opus
  beats Haiku. So the defensible claim is bounded: *Haiku is sufficient for the
  judgment shapes measured here*, **not** *Haiku equals Opus at the reasoning
  frontier*. If tasks exist where Opus's depth is worth 3×, this set hasn't captured
  them.
- **Cost is the token-price ratio**, not measured wall-clock latency.

### 2. Audits stop hallucinating findings

An earlier wave (wave-24) exposed a real failure mode: an all-Haiku audit reported
four P0 issues, **zero of which were real**. The release audit for this RC was run
with **adversarial verification of every finding** and closed with **0 hallucinated
issues**.

**Honest limit:** it is an internal audit. No third party has re-run it.

### 3. The kill-switch actually aborts a wave

A halt control is only worth the claim if it's proven. The fleet-wide kill-switch
([`tools/halt.py`](../tools/halt.py)) is wired into the **live dispatch path** and was
exercised end-to-end: a single signal **aborted a real wave with zero workers
spawned**.

**Honest limit:** it is operator-triggered — a manual brake a human pulls, not an
autonomous safety monitor that trips on its own.

### 4. A cost ceiling brakes runaway spend

A per-wave spend ceiling ([`tools/cost_ceiling.py`](../tools/cost_ceiling.py)) halts
dispatch when the configured budget is exceeded.

**Honest limit:** it brakes against a **configured** ceiling; it is **not yet tied to
live token spend**. It's a guardrail you set, not a live meter.

### 5. The package installs and reproduces from a clean clone

The npm tarball is a **~409 kB** package (measured via `npm pack`) that **builds and
validates from a fresh clone in CI** — the `reproduce` workflow proves the tarball is
self-contained. The dashboard views shipped this RC (Wave PR Board, Agent Inspector)
were **browser-proven** with Playwright. Steps to reproduce offline:
[docs/reproduce.md](./reproduce.md).

**Honest limit:** the offline reproduce job runs the test suites and the benchmark
*scorer*; it does **not** run real-model benchmark calls or the Playwright browser
proofs, which need local setup and (for the models) API keys.

## The limits the project owns, in one place

None of these are hidden, because hiding them would betray the one thing the project is
actually selling — candor:

1. **The benchmark is curated (N=39), not transcript-sampled.** Directional, not
   definitive; it maps a floor, not the frontier.
2. **The real dispatch core is out-of-repo.** It runs in the Claude Code harness. This
   package is the harness around it, not a turnkey autonomous runtime.
3. **No third party has reproduced any of it yet.** The artifacts are committed so a
   skeptic can — that is transparency, not a substitute for independent replication.
4. **The cost ceiling is a brake, not a live meter.** It enforces a configured budget,
   not measured spend.
5. **It is local-first.** State lives in git and local files; there is no hosted
   control plane. Team scale beyond one machine is on the roadmap, not shipped.
6. **It is a release candidate.** APIs, config, and dashboard contracts may still shift
   before 0.1.0. Pin the exact version if you need stability.

## Where to check the receipts

- [`bench/results/`](../bench/results/) — dated benchmark runs (v1 extraction, v2 & v3
  judgment), each with its own honest interpretation and limits.
- [`bench/README.md`](../bench/README.md) — why the benchmark exists and how the scoring
  removes agents from the grading loop.
- [`tools/halt.py`](../tools/halt.py), [`tools/cost_ceiling.py`](../tools/cost_ceiling.py)
  — the kill-switch and cost-ceiling implementations.
- [docs/reproduce.md](./reproduce.md) — reproduce the tests and benchmark scorer from a
  clean clone.
- [RELEASE-NOTES.md](../RELEASE-NOTES.md#honest-limits) — release-level "Honest limits".
- [CHANGELOG.md](../CHANGELOG.md) — the full `0.1.0-rc.1` entry.

---

The one-line version: **the fleet built, tested, audited, and benchmarked its way to a
shippable release candidate, and every claim about that comes with a committed artifact
and a stated limit.** That combination — self-built *and* honest about the seams — is
the milestone.
