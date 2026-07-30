# bench/ — Held-out benchmark scaffold

## Why this exists

Wave-25's autonomy expansion (self-merging portfolio PRs on green CI) rests on a
claim that has never been independently checked: **"Haiku is sufficient for fleet
subagent work, quality-equivalent to Sonnet/Opus."** The only evidence for that
claim to date has been the fleet grading its own output — agents judging agents —
with results recorded in a private `MEMORY.md`. That is not evidence a skeptical
outside reader can check. It is agents vouching for agents.

This directory, together with `tools/bench_runner.py`, is the **measurement
apparatus** for closing that gap: a small, fixed, held-out set of tasks with
answers checked by a plain Python string/regex comparison — no model, no agent,
no fleet member in the scoring loop.

**This wave does NOT run the comparison.** No Haiku-vs-Sonnet-vs-Opus numbers are
produced or claimed here. This PR ships the harness, the tasks, and the ground
truth, proven correct against a zero-cost mock runner. Producing an actual verdict
("Haiku scores X%, Opus scores Y%") is separate future work that spends real
tokens against real models and should be reported as its own dated result, not
folded into this scaffold.

## Fixture intent manifest

Deliberately-broken fixtures used for AI bug-detection benchmarks and mutation-testing validation are tracked in `fixtures-intent.json`. Each entry documents:
- **path** — fixture file location (repo-relative)
- **reason** — why this fixture is intentionally broken/incomplete
- **fixture_type** — `deliberately_broken_code` (tests fail by design) or `intentional_coverage_gap` (correct code with test gaps)
- **added** — ISO date when fixture was added

This manifest allows CI and analysis tools to distinguish benchmark fixtures from real regressions. Validate with:

```bash
python tools/fixture_intent_check.py --root .
python tools/fixture_intent_check.py --root . --json  # JSON output for CI
```

Fixtures included:
- `tests/fixtures/seam_s_sample_task/repo/test_sample.py` — add() returns x*y (tests fail)
- `tests/fixtures/seam_sample_task/repo/main.py` — count() has off-by-one (tests fail)
- `bench/fixtures/mutation_fault_fixture.py` — correct code with intentional coverage gaps
- `bench/fixtures/test_mutation_fault_fixture.py` — incomplete test suite (mutation tool validation)

## What's in here

- `tasks.jsonl` — 12 held-out tasks, one JSON object per line. Each task has:
  - `id` — stable identifier
  - `category` — what kind of subagent work it represents
  - `match` — `"exact"` or `"regex"`, tells the scorer how to check the answer
  - `prompt` — the full text sent to a model runner
- `ground_truth.jsonl` — one JSON object per line, keyed by `id`:
  - exact tasks: `{"id": ..., "expected": "<string>"}`
  - regex tasks: `{"id": ..., "expected_regex": "<pattern>"}`
- `frontier_eligibility.py` — Frontier slice eligibility checks (task difficulty thresholds).
- `probe_refusals.py` — Refusal-probe harness: detects model refusals on edge-case prompts.
- `run_seam_s.py` — Seam study runner (Sonnet model).
- `run_seam_u.py` — Seam study runner (user-specified model).
- `METHODOLOGY.md` — Pre-registration record, amendments, and scoring methodology.
- `RESULTS-INDEX.md` — Index of all dated benchmark result files.
- `SEAM-STUDY-PREREG.md` — Pre-declared design, success criteria, and ceiling rule.
- `seam_tasks/` — Seam study task definitions and ground truth.
- `ext120/` — Extended 120-task benchmark set and ground truth.
- `tools/bench_runner.py` (repo root `tools/`, not under `bench/`) — the scorer
  and CLI. See below.

## Task selection

The 12 tasks are meant to be representative slices of what fleet subagents
actually do day to day, not a general-capability IQ test:

| category | represents |
|---|---|
| `classify_file_change` (t01-t04) | "what kind of file is this diff touching" triage, used to route review lenses |
| `extract_test_name` (t05) | pulling a failing test's name out of a CI log to file/re-dispatch a fix |
| `extract_issue_number` (t06) | linking a commit back to its tracked item |
| `classify_pr_title` (t07) | conventional-commit categorization for changelog/release tooling |
| `extract_exception_type` (t08) | triage-by-exception-class from a traceback |
| `is_real_bug_judgment` (t09) | the actually-hard case: does a reviewer's finding hold up — semantic judgment, not string matching |
| `extract_version` (t10) | parsing a CHANGELOG entry |
| `transform_snake_to_camel` (t11) | small deterministic text transform, the kind of thing a "just run sed" step gets delegated as |
| `extract_file_line` (t12) | pulling a `path:line` locator out of a log line |

Eleven of the twelve tasks have an objectively checkable, unambiguous answer.
One (`t09`) is a genuine judgment call included on purpose — most of what
"is this finding real" review work looks like — and it is the one task the
shipped mock runner gets wrong (see below), by design, to prove the scorer
actually discriminates between correct and incorrect answers rather than
rubber-stamping everything.

## How to run it

Offline, zero-cost, no API key, no network — runs the mock runner and prints
a table:

```bash
python tools/bench_runner.py
```

```
Benchmark results -- runner: mock
------------------------------------------------------------
id    category                    result
t01   classify_file_change        PASS
...
t09   is_real_bug_judgment        FAIL
...
------------------------------------------------------------
Accuracy: 11/12 = 91.7%
```

That 91.7% is a fixture of the mock heuristic's regex-parsing logic. **It is
not a claim about any real model.** The mock runner cannot perform semantic
judgment; it is a stand-in built only to prove the scoring pipeline works.

Override the task/ground-truth files (useful for local experimentation or
CI isolation):

```bash
python tools/bench_runner.py --tasks path/to/tasks.jsonl --ground-truth path/to/gt.jsonl
```

Programmatic use:

```python
from tools.bench_runner import load_tasks, load_ground_truth, run_bench

tasks = load_tasks()
ground_truth = load_ground_truth()
results, accuracy = run_bench(tasks, ground_truth, my_runner)
```

## Running against real models (wave-32+)

### Claude CLI runner

The benchmark ships with built-in runners for the local `claude` CLI (Anthropic's
official Claude Code command-line tool). If `claude` is installed and authenticated,
you can run the benchmark against real models without writing any custom runner code:

```bash
# Install claude if not already installed
npm install -g @anthropic-ai/claude-code

# Run the benchmark against Haiku, Sonnet, and Opus
python tools/bench_runner.py --runner haiku
python tools/bench_runner.py --runner sonnet
python tools/bench_runner.py --runner opus
```

The CLI runner:
- Shells `claude -p "<prompt>" --model <alias> --output-format json`
- Accepts model aliases: `haiku`, `sonnet`, `opus` (or full model ids)
- Captures wall-time latency (in milliseconds) alongside accuracy and tokens
- Gracefully fails with a clear error if `claude` is not on PATH

Example output (with cost data):

```
Benchmark results -- runner: haiku
------------------------------------------------------------------------
id    category                    result   tokens     latency_ms
t01   classify_file_change        PASS     42         125.3
t02   classify_file_change        PASS     38         118.2
...
------------------------------------------------------------------------
Accuracy: 11/12 = 91.7%
Cost:     total_tokens=512 avg_tokens/task=42.7 total_latency_ms=1450.5 avg_latency_ms=120.9
```

### Custom model runners

`run_bench()` takes any `Callable[[str], str]` or `Callable[[str], tuple]` — a
function that accepts a task `prompt` and returns either the model's raw text
response or a `(text, usage)` pair. To score a model beyond the built-in runners,
write a runner that calls it and register it, e.g.:

```python
# tools/bench_runner.py (or a separate script that imports it)
import anthropic
import time

_client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

def custom_runner(prompt: str) -> tuple:
    start = time.time()
    resp = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=64,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed_ms = (time.time() - start) * 1000
    text = resp.content[0].text
    usage = {
        "tokens": resp.usage.output_tokens,
        "latency_ms": elapsed_ms,
    }
    return (text, usage)

RUNNERS["custom"] = custom_runner
```

Then run `python tools/bench_runner.py --runner custom` and compare the printed
accuracy tables side by side.

## Honest limits

- **N=12 is small.** A handful of percentage points of difference between
  models on a 12-task set is noise, not signal. Do not report deltas smaller
  than a few tasks' worth of accuracy (roughly 8 percentage points here) as
  meaningful without a much larger task set or repeated sampling.
- **Task selection bias.** These 12 tasks were picked by one author (this
  wave's implementer) reasoning about "what fleet subagents do," not sampled
  from actual fleet transcripts. They may not represent the true task
  distribution, and they skew toward extraction/classification (regex- or
  string-checkable) because those are what a programmatic, agent-free scorer
  can grade. Only one task (`t09`) requires genuine semantic judgment — real
  subagent work almost certainly has a higher proportion of judgment calls
  than that, so this benchmark likely *overstates* how well a cheap
  extraction-shaped heuristic (or a cheap model) would do on the full mix of
  real fleet work.
- **Real model runs are available (v1, 2026-07-16; extended v2/v3 judgment runs).** The 12-task
  extraction set (v1) was run blind on 2026-07-16 across all three models; results in
  `bench/results/2026-07-16-haiku-sonnet-opus.md`: Haiku 12/12, Sonnet 12/12, Opus 12/12
  (100% all models). The offline mock runner is now used for **offline development and CI
  isolation**, where real model API calls are not feasible. Link to the full interpretation
  and extended judgment runs (v2+v3, 39 tasks) in `bench/INTERPRETATION.md`.
- **Exact/regex match is a narrow rubric.** It can't credit a correct answer
  phrased differently (e.g., "Yes, it's real" vs. "yes"), and it can't detect
  a right-answer-for-the-wrong-reason. It is deliberately narrow because the
  point is removing agents from the grading loop, not building a lenient
  judge (a lenient judge reintroduces exactly the "agent grades agent"
  problem this scaffold exists to avoid).
- **Not a security/safety benchmark.** This says nothing about adversarial
  robustness, prompt injection resistance, or anything outside plain task
  accuracy.

## Transcript-sampled benchmark (Phase 1)

Beyond the curated 12-task benchmark, you can extract CODING TASKS from real
Claude Code session transcripts. This allows the benchmark to grow beyond
hand-written examples and measure real-world developer-agent task distributions.

**Phase 1 (this wave):** The sampler infrastructure and tests; no actual grading run.

### The sampler

`bench/sample_transcripts.py` extracts completed CODING TASKS from Claude Code
session JSONL files (transcripts of user interactions + agent tool calls).
It identifies:
  - User prompts asking the agent to write/implement code
  - Agent responses containing code (Python, JavaScript, bash, etc.)
  - The produced code and the original task prompt

Each extracted task is emitted in bench format:
```json
{
  "id": "sampled_abc12345",
  "category": "transcript_sampled_coding_python",
  "match": "exact",
  "prompt": "<redacted user prompt>",
  "produced_code": "<redacted code the agent wrote>",
  "needs_grader_authoring": true
}
```

The `needs_grader_authoring` field signals whether the task has a **checkable
specification** (test cases, assertions, examples in the prompt). If true, a
human must write the hidden test cases before this task is gradeable. If false,
the task has a clear spec and is ready for grading (pending external reference
model runs).

**Honest limitation:** Sampled tasks are not automatically verified to be
correct, and most will need grader authoring. This phase establishes the
infrastructure and proves it works; the actual verdict ("Haiku scores X% on
real production tasks") comes in a later wave that includes running reference
models and writing the missing test cases.

**Sanitization is critical.** The sampler aggressively removes PII and credentials
before outputting tasks, because the sampled task set lives in a public repository:
  - Absolute paths (→ `<path>`)
  - Usernames, email addresses (→ `<email>`, `<username>`)
  - API tokens, secrets (→ `<api_key>`)
  - Any context that could leak customer data

Usage (on your private transcript collection; results go to private outputs):

```bash
# Sample up to 100 coding tasks from your private transcripts directory
python bench/sample_transcripts.py \
  --transcripts-dir /path/to/your/transcripts \
  --output bench/tasks_sampled.jsonl \
  --max-tasks 100

# Output shows how many sampled tasks need grader authoring:
# "  N/M need grader authoring"

# To grade sampled tasks, you must provide ground truth (the hard part):
# Edit bench/tasks_sampled.jsonl to add expected outputs, then create
# a matching bench/ground_truth_sampled.jsonl with id + expected/expected_regex.

# Score the sampled benchmark against a model:
python tools/bench_runner.py \
  --runner haiku \
  --tasks bench/tasks_sampled.jsonl \
  --ground-truth bench/ground_truth_sampled.jsonl
```

### Limitations of transcript sampling

- **Sampling bias:** Tasks are sampled from whatever agents actually did, which
  may not be representative of the full distribution of possible work.
- **Needs authoring:** Most sampled tasks lack a checkable spec. Before they can
  be graded, a human (or reference model) must supply the expected output / hidden
  test cases. Silent truncation of ungradeable tasks would recreate the exact
  "trust me" gap this benchmark exists to avoid, so we mark them explicitly.
- **Ground truth is the bottleneck:** Extracting tasks is fast; defining correct
  answers for each is slow. Plan accordingly.
- **No automatic verification:** Unlike the curated 12-task set, sampled tasks
  are not pre-vetted for correctness or representativeness.

### Latency tracking (wave-32+)

The bench runner now records wall-time latency for each task, alongside accuracy
and token usage. This enables cost-quality analysis: "Haiku at X% accuracy, Y
tokens/task, Z ms/task — Sonnet at X'% accuracy, Y' tokens/task, Z' ms/task."

Runners can return latency in two ways:

```python
# Option 1: bare text response (backward-compatible, no cost data)
def my_runner(prompt):
    response = client.messages.create(...)
    return response.content[0].text

# Option 2: (text, usage) tuple with tokens and latency
def my_runner_with_metrics(prompt):
    import time
    start = time.time()
    response = client.messages.create(...)
    elapsed_ms = (time.time() - start) * 1000
    text = response.content[0].text
    usage = {
        "tokens": response.usage.output_tokens,
        "latency_ms": elapsed_ms
    }
    return (text, usage)
```

The scorer automatically extracts and reports:
  - Per-task latency (in the results table, if any task reports it)
  - Average latency and total latency across all tasks
  - Side-by-side comparison with other models

Example output:

```
Benchmark results -- runner: haiku
------------------------------------------------------------------------
id    category                    result   tokens     latency_ms
t01   classify_file_change        PASS     42         125.3
t02   classify_file_change        PASS     38         118.2
...
------------------------------------------------------------------------
Accuracy: 11/12 = 91.7%
Cost:     total_tokens=512 avg_tokens/task=42.7 total_latency_ms=1450.5 avg_latency_ms=120.9
```

This latency data is strictly observational — it describes *your* execution
environment (network, model load, time of day, etc.), not the model itself.
Do not interpret a single run's latency as a model property.

## Frontier discrimination slice (Phase 2)

**Goal:** Test whether the benchmark can *discriminate* between model tiers
(Haiku vs Opus) or whether it's ceiling-bound (both models score ~40/40).

The extraction-only benchmark (v1, 12 tasks) showed no separation — all models
scored 12/12 (100%). Extended with judgment-shaped tasks (v2+v3 combined, 39 tasks):
Haiku/Sonnet 39/39 vs Opus 38/39 (one divergence, Opus on v2 j11). Even the harder
judgment set converged. The frontier slice builds an even harder task set (~20 tasks)
designed to expose tier differences:

- **Multi-step reasoning** (SQL refactoring, boolean logic, configuration merging)
- **Subtle defect detection** (race conditions, type coercion, format-string vulns)
- **Long-context needle** (find contradictions in 2500-word spec)
- **Semantic judgment** (instruction conflict resolution, state machine safety)

Each task carries a `discrimination_rationale` field explaining why a weaker model
should struggle (e.g., "Requires understanding TOCTOU race conditions, which Haiku
often misses; Opus catches them precisely").

### Usage

Offline (zero-cost, canned responses for validation):
```bash
python bench/frontier_slice.py --mode offline
```

Live (against real models, cost-gated):
```bash
# Estimate cost, then exit without --confirm-spend
python bench/frontier_slice.py --mode live --model claude-3-5-opus-20241022

# Actually run (requires ANTHROPIC_API_KEY + --confirm-spend)
python bench/frontier_slice.py --mode live --model claude-3-5-opus-20241022 --confirm-spend
python bench/frontier_slice.py --mode live --model claude-3-5-haiku-20241022 --confirm-spend
```

### Output format

Same as the main benchmark: `tasks_frontier.jsonl` (prompt + discriminator rationale),
`ground_truth_frontier.jsonl` (id + expected/expected_regex), and `frontier_slice.py`
(runner, offline/live modes, cost gating).

Scoring is deterministic: regex or exact-match against ground truth. No model in the
scoring loop.

### Expected results

- **Haiku**: ~50–70% accuracy (ceiling on multi-step reasoning, misses subtle defects)
- **Opus**: ~80–95% accuracy (handles most judgment tasks, catches edge cases)

If both models score similarly on this slice, the frontier slice itself needs revision
(tasks were not hard enough). If a large gap appears, that gap is evidence the
benchmark can measure tier separation.

### Limitations

- Small N (~20 tasks); a few percentage points is noise, not signal
- Task selection bias: hand-authored for "hard" not sampled from real workflows
- Scoring cannot credit "right for wrong reason" — semantic judgment ground truth
  must be explicit (not just test-case checking)
- No safety/adversarial robustness measurement

This phase is validation infrastructure, not a final verdict. The benchmark is now
measurable; the question "is Haiku sufficient for fleet work?" remains open pending
live runs and result analysis.

### Scoring Honesty: Regex Tightening & Exemplar/Counter-Example Validation

To prevent keyword-permissive scoring (where a weak answer passes by containing a
common word), all 20 `expected_regex` patterns have been tightened to require
*conjunction* of discriminating elements:

- Use lookaheads `(?=.*element1)(?=.*element2)` to ensure multiple key concepts coexist
- Avoid single-word alternatives (e.g., bare `|minimal` or `|break|problem`)
- Require specific mechanisms or justifications, not just terminology

Example: **ft15 (state machine correctness)**
- OLD (keyword-permissive): `break|problem|invariant|skip.*PROCESSING|notification|cleanup|audit`
  - Fails: A response saying "the transition breaks PROCESSING" passes even without identifying side effects
- NEW (substance-required): `(?i)(?=.*break|violat)(?=.*PROCESSING)(?=.*skip)(?=.*(?:notification|cleanup|audit|invariant))`
  - Requires: explicit identification of the problem + PROCESSING + skip mention + at least one side effect

Every regex ground truth now includes two validation fields:
- `exemplar`: a correct answer that *must* match the regex (tests scorer permissiveness)
- `counter_example`: a plausible wrong answer that *must NOT* match the regex (tests scorer strictness)

Validation is machine-checked: `tests/test_frontier_slice.py::TestGroundTruthValidation::test_regex_ground_truth_has_exemplar_and_counter` asserts that every exemplar matches and every counter_example fails.

**Offline accuracy with tightened patterns: 55.0% (11/20)** — represents the FakeTransport mock runner's performance against substance-demanding patterns. The mock runner is weak on semantic judgment and multi-step reasoning; this reflects its limited instruction-following, not a ceiling for real models. (P2 gate-2 round-2 fix: broadened ft08 and ft15 synonym alternations to reduce false negatives.)
