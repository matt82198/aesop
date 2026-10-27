# Benchmark Honesty: Why Aesop's Benchmark Breaks the Marketing Mold

Every AI coding tool benchmark is fake.

Not "slightly overstated" or "cherry-picked." Fake. The model grades its own homework (circle-jerk validation). The tasks are drawn from the vendor's domain (selection bias by default). Success is defined backwards ("our benchmark shows we win"). The sample size is never disclosed. The confidence intervals are missing. And the whole thing is locked in a blog post you can't reproduce.

Aesop's benchmark breaks this pattern. It's not perfect, but it's honest. Pre-declared success criteria. Deterministic grading (Python, no model). Committed tasks anyone can run. Explicit caveats woven into every claim.

Here's why this matters and how we did it.

## Why Existing Benchmarks Fail

The problem is not malice; it's incentives. A benchmark that shows "our model wins" goes in the marketing deck. A benchmark that shows "our model loses on hard tasks" stays in a GitHub comment. The vendor's benchmark is a trophy, not an instrument.

Classic failures:

1. **Agent self-grading.** "Did the agent complete this task?" The agent answers. Result: 99% pass rate and zero insight.

2. **Task selection bias.** "Here are 5 tasks we designed that our model is good at." No sampling from real-world task distribution.

3. **Missing confidence intervals.** "Our model scores 85%." On what? On a sample of 1? On 1,000? With what variance?

4. **Vague success criteria.** "The model correctly solves the problem." What counts as correct? A model's opinion? A regex? A human eyeball?

5. **Locked-up tasks.** "We have a proprietary benchmark." Okay, but now I can't reproduce it or run it on my own model. You're just asking me to trust you.

## Aesop's Approach: Pre-Declaration + Deterministic Grading

### Pre-Declared Success Criteria

Before running any model, I wrote down the stopping rule: **"When 2 or more model tiers score ≥92% accuracy on the task set, the instrument has failed to discriminate."**

This rule fires if Haiku and Sonnet both ace the benchmark—meaning the benchmark measures a floor (Haiku is sufficient for this domain), not a separating frontier. It's not a failure of Haiku; it's a failure of the instrument to measure what I was trying to measure.

Result on the 39-task judgment set (v3 extended): **Haiku 39/39, Sonnet 39/39, Opus 38/39.** The ceiling rule fires. Both Haiku and Sonnet achieved 100%. Honest interpretation: **the benchmark maps a sufficiency floor (Haiku is good enough for scoped judgment tasks), not tier equivalence.** That is not marketing; that is naming what the data actually shows.

### Deterministic Grading: No Model in the Loop

Every task has ground truth defined by Python regex or exact string match. No model decides if the answer is "good enough." No human eyeballs the output. No scoring randomness.

Example task (`is_real_bug_judgment`): Given a code review finding, does the reviewer's bug report hold up?

- **Exemplar** (must match): "Incorrect: The find() method is case-sensitive, but the code treats it as case-insensitive. On line 42, the search for 'foo' will fail if 'Foo' is provided. This violates the documented contract."
- **Counter-example** (must NOT match): "The code has a bug."
- **Regex ground truth**: `(?i)(?=.*case.sensitive|case.insensitive)(?=.*line\s+\d+)(?=.*violat|incorrect|bug)` — Requires multiple specific elements, not just keyword soup.

If a model's response matches the regex, it scores 1. If not, it scores 0. No judgment call, no "close enough." This is deliberately narrow because the point is removing agents from the grading loop, not building a lenient judge.

### Committed Tasks Anyone Can Run

The 39 tasks live in `bench/tasks.jsonl` (checked into git, versioned). Ground truth in `bench/ground_truth.jsonl`. A runner that takes any callable and scores it:

```bash
# Score Haiku
python tools/bench_runner.py --runner haiku

# Score your own model
python tools/bench_runner.py --runner custom --custom-endpoint http://localhost:8000/v1
```

No proprietary setup, no closed-source grader. Clone the repo, run the benchmark yourself, see the results.

### Transparent Caveats Woven Into Claims

Instead of hiding limitations, I state them in the same breath as the claim:

- **"Haiku 39/39 vs Opus 38/39."** On *scoped judgment tasks (code review, severity calibration, root-cause analysis, refactor equivalence)*—not frontier reasoning or long-horizon planning.
- **"N=39 is directional, not proof."** On a 12-task benchmark, a few percentage points is noise. On 39 tasks, confidence is higher but still bounded.
- **"Task selection bias."** These 39 tasks were curated (hand-authored for "representative"), not sampled from real fleet transcripts. The distribution may not reflect true fleet work.
- **"Seam-level only."** Agents operate within the seam (local orchestration, code review). Frontier tasks (architecture redesign, novel algorithms) are out of scope and will underperform.
- **"Ceiling-bound on the judgment set."** Both Haiku and Sonnet hit 39/39. This instrument cannot separate them on this task domain. That is not a bug; it is data.

## Design Decisions Explained

### Decision 1: Ceiling Rule

Most benchmarks stop at "here's the winner." I added an explicit failure mode: if two model tiers converge on the same answer, the benchmark failed to discriminate, and I must either admit it or redesign.

Why? Because the question I'm trying to answer is "Is Haiku sufficient for fleet work?" A benchmark where everyone scores 95% tells me: "Yes, sufficiency floor, but I don't know if there's a frontier gap." That is useful information, but I must name it as such.

### Decision 2: Python Regex + Exact Match

Scoring by string matching is intentionally rigid. It cannot credit "right answer, wrong phrasing." Example: if the ground truth expects "Yes, the bug is real" and the model says "Affirmative, the finding holds," it fails.

This is by design. A lenient grader reintroduces the "agent grades agent" problem I'm trying to avoid. Rigid scoring means I cannot arbitrarily move the goalposts after running the model.

### Decision 3: Pre-Registration

Before running any real model, I published `bench/SEAM-STUDY-PREREG.md`: the design, success criteria, and amendments. Post-hoc amendments are marked with timestamps. Readers can see exactly what changed between "plan" and "result."

If I had defined success criteria *after* seeing Haiku's results, it would be goalpost-moving. Pre-registration makes goalpost-moving visible.

### Decision 4: Curated, Not Sampled

The 39 tasks are hand-authored examples of "seam-level work," not a random sample from real transcripts. This introduces selection bias: they skew toward extraction and classification (regex-checkable) because those are what a programmatic scorer can grade. Real fleet work probably has a higher proportion of semantic judgment calls.

Honest framing: **this benchmark likely overstates how well Haiku would do on the full mix of real fleet work.** I'm measuring a floor, not an average.

## The Numbers, Stated Carefully

**Judgment tasks (39 total):**

| Model | Accuracy | Cost (vs Opus) |
|---|---|---|
| Haiku | 39/39 (100%) | 1× |
| Sonnet | 39/39 (100%) | 3× |
| Opus | 38/39 (97.4%) | 5× |

**Honest interpretation:**
- Both Haiku and Sonnet converged on the task set.
- Opus erred once (severity calibration task, false negative on a subtle edge case).
- The benchmark shows *sufficiency* (Haiku is good enough for this domain) *at a specific scope* (scoped judgment tasks with explicit rubric), *on a curated set* (hand-authored, not sampled).
- The frontier (where Opus might pull ahead) is not tested here. Frontier tasks (novel algorithms, 100+ step chains) are out of scope.

**Cost model leverage:** Using Haiku instead of Opus cuts orchestration cost from $0.05-0.10 per wave to $0.01-0.02. That cost difference determines whether running 30+ waves in 18 days is viable or burns through budget.

## What Makes This Honest

1. **Pre-declared ceiling rule** — I can't move goalposts after seeing results.
2. **Committed tasks** — Anyone can run the benchmark and verify.
3. **Deterministic grading** — No model in the loop, no human judgment creep.
4. **Published N, confidence caveats** — "39 tasks is directional; don't read deltas smaller than 8 percentage points as signal."
5. **Scope explicitly bounded** — "Seam-level only, not frontier."
6. **Selection bias named** — "Curated, not sampled from real transcripts."
7. **Failure mode is public** — "If 2+ tiers converge, the instrument failed; we report that, not hide it."

## What It Doesn't Claim

- Haiku = Opus on all tasks (false; frontier reasoning is not tested).
- The benchmark is representative of all fleet work (false; it's curated toward extractable tasks).
- 39 tasks is statistically significant (false; it's directional).
- This instrument will separate models forever (false; as models improve, the ceiling rule trips more often).

## Why This Matters for the Ecosystem

Honest benchmarks are boring marketing. They don't generate headlines like "Our model is 10% better than everyone else's."

But they are load-bearing for engineering. When I decided to use Haiku-only dispatch (vs hierarchical Sonnet supervisors), I needed to know if Haiku was good enough. A fake benchmark ("Haiku scored well on tasks we designed for Haiku") would have lied. An honest benchmark ("Haiku converged with Sonnet on this task domain; you're trading off frontier capability for cost") let me make an informed decision.

The cost model is a direct result of that honest number. If Haiku had scored 60% on the judgment set, I would have designed differently. If it had scored 95%, I would have used it everywhere (and I did, because it did).

That is how measurement should work: it shapes decisions, not marketing copy.

## Try It Yourself

```bash
git clone https://github.com/matt82198/aesop.git
cd aesop

# Run the zero-cost mock benchmark (91.7% accuracy, proves scorer works)
python tools/bench_runner.py

# Run against real models (requires ANTHROPIC_API_KEY)
python tools/bench_runner.py --runner haiku
python tools/bench_runner.py --runner opus

# Read the pre-registration and amendments
cat bench/SEAM-STUDY-PREREG.md

# Read the full analysis and ceiling-rule trip
cat bench/results/2026-07-26-judgment-v3-ceiling-addendum.md
```

All tasks are committed. All ground truth is in git. If you clone the repo and run the benchmark, you get the same numbers we report—or you find a bug in our grading (and you can open a PR to fix it).

---

**References:**
- `bench/SEAM-STUDY-PREREG.md` — Pre-registration and amendments
- `bench/results/2026-07-26-judgment-v3-ceiling-addendum.md` — Ceiling rule trip and honest interpretation
- `bench/METHODOLOGY.md` — Full benchmark design, limitations, and discrimination analysis
- `bench/results/seam-loop-study-2026-07-28.md` — Repair loop data (separate study, same honesty discipline)
