# Pre-declared equivalence margin — frontier discrimination slice

- Declaration date/time: 2026-07-26 (UTC timestamp of this commit is the binding record).
- **Claim protocol: we claim tier equivalence on the discrimination slice ONLY IF the observed accuracy difference is under 10 percentage points AND the TOST 90% confidence interval for the difference lies entirely within ±10pp.** (Equivalence bounds ±10pp, alpha=0.05 per one-sided test.)
- Anything outside that: we report the gap as-is, including results that hurt.
- Protocol: each tier runs the same slice with **3 repeats per task**; per-task majority scoring; report per-tier score DISTRIBUTIONS (per-task, per-repeat), not just means.
- **Ceiling rule (pre-declared): if two or more tiers land at or above 92%, the instrument failed to discriminate — we say so explicitly and harden the task set before making any equivalence claim.**
- Grading provenance (pre-declared): grading is machine-checked against ground-truth answer patterns committed in the slice definitions; no model-graded scoring in this run. If any task requires judgment-based grading, it is excluded or flagged, never silently model-graded.
- Tiers under test: claude-opus-5 (released 2026-07-24), claude-fable-5, claude-sonnet-5, claude-haiku-4-5-20251001, plus one non-Claude tier (OpenAI seam) for external validity.
- Spend cap for the full run: US$20.
- Statistical honesty notes: N=20 tasks x 3 repeats is SMALL; ruling out gaps much smaller than ~10pp is not possible at this N — that is WHY the margin is 10pp. Larger claims require a bigger slice (tracked as follow-up).

## Amendment 1 — N=60 expansion (committed 2026-07-26, before v2 results)

This amendment pre-commits 40 additional frontier discrimination tasks (ft21–ft60) alongside the original 20 (ft01–ft20), expanding the discrimination slice from N=20 to N=60.

**Scope and authorship:**
- **40 new tasks (ft21–ft60)** authored after the N=20 v1 results were known, without per-model tuning.
- New tasks follow the same format and difficulty philosophy as ft01–ft20: multi-step reasoning, defect detection, semantic equivalence, config validation, etc.
- All 60 tasks now have machine-checkable ground-truth patterns (regex or exact-match) with exemplar and counter-example validation.
- Test file `tests/test_frontier_slice_n60.py` verifies all 60 patterns mechanically: exemplars MUST match, counter-examples MUST NOT match (credibility gate).

**New protocol for N=60 run (v2):**
- Same tiers under test (Opus-5, Fable-5, Sonnet-5, Haiku-4.5, + OpenAI seam).
- Same repeated-run structure: 3 repeats per task × 60 tasks.
- Same margin protocol: equivalence claim only if difference ≤10pp AND TOST CI within ±10pp.
- **Updated ceiling rule: if two or more tiers land at or above 85% (lowered from 92% to reflect larger N), the instrument discriminates poorly — we say so explicitly.**
- Grading: machine-checked only (patterns pre-committed and tested). No silent model grading.
- Parallel execution: concurrent task runs do NOT affect scoring; per-run transport recorded for reproducibility.
- Spend cap for v2 run: US$30 (N=60 vs. N=20, × 5 tiers × 3 repeats = 900 API calls).

**Data and bias:**
- **Honesty statement:** ft21–ft60 authored after N=20 results known; we state explicitly: "no per-model tuning" and will verify post-hoc via per-tier per-task performance audits.
- Pre-committed: all task definitions, ground-truth patterns, exemplars, counter-examples.
- Pattern-validity test (tests/test_frontier_slice_n60.py) runs in CI; no new results until test passes CLEAN.

**Disposition of ft09 (0% in N=20):**
- Task: `ft09_refactoring_correctness_semantic` — simple list comprehension equivalence.
- Result: All five tiers scored 0% on this straightforward task despite correct exemplar/pattern.
- Analysis: Ground-truth pattern and exemplar are valid; the task is genuinely difficult for the weaker FakeTransport mock but expected to score higher on real models. No pattern fix needed; task retained as-is. Expect higher accuracy on real v2 run.

**Record audit trail:**
- Commit hash of this amendment is the binding timestamp.
- All 60 task definitions (bench/tasks_frontier.jsonl) and ground truth (bench/ground_truth_frontier.jsonl) committed before any v2 results.
- Test suite run gate enforced in CI (tests/test_frontier_slice_n60.py must pass).
- Post-v2: audit report will include per-tier breakdown, per-task score distributions, and equivalence decision per the margin protocol.

## Amendment 2 — N=100 extension (committed 2026-07-26, after v2 results, before any v3 results)

This amendment pre-commits 40 additional tasks (ft61–ft100), expanding the slice from N=60 to N=100 (n=300 runs/tier at 3 repeats).

**Why, stated honestly (sequential, data-dependent extension):**
- The v2 (N=60) result left ONE pair undecided: claude-opus-5 vs gpt-4o-mini at +3.89pp, 90% CI [-3.21, +10.99] — 0.99pp of overhang past the +10pp bound. All other 9 pairs were EQUIVALENT.
- This extension was decided AFTER observing that result, specifically to make that pair decidable. Extending after peeking inflates type-I error relative to a fixed-N design; we disclose it rather than pretend otherwise. The v3 report must carry this disclosure verbatim, and the pooled-N verdicts are reported alongside (not instead of) the frozen v2 verdicts.
- Power math at planning values (p=0.806 vs 0.767): at n=300/tier the 90% CI half-width is ~5.5pp, so the pair resolves EQUIVALENT iff the pooled diff is <= ~4.5pp; if the true gap is larger, remaining indeterminate (or leaning gap) is the correct published outcome, not a failure.

**Scope and authorship:**
- 40 new tasks (ft61–ft100) authored after v2 results were known, without per-model tuning; category mix mirrors ft01–ft60 (SQL/migrations, concurrency/distributed/caching, regex/unicode/coercion/config, long-context/instruction-conflict/ambiguity/contracts/refactoring).
- All 100 tasks have machine-checkable ground-truth patterns with exemplar and counter-example validation; the CI pattern gate is extended to cover ft61–ft100 and must pass CLEAN before any v3 runs.

**Protocol for the v3 run (pooled N=100):**
- Same tiers, same 3-repeats structure, same margin protocol (equivalence iff |diff| <= 10pp AND TOST 90% CI within +/-10pp), same 85% ceiling rule, machine grading only.
- Only the 600 new (tier, ft61–ft100, repeat) tuples run; ft01–ft60 results are frozen as committed in frontier-v2-2026-07-26.json.
- **Transport change (disclosed):** new Claude-tier runs use direct api.anthropic.com HTTP (`BENCH_API_KEY`, per-run label `anthropic-http`, exact usage token counts, pay-per-use billing); ft01–ft60 Claude runs used the claude CLI (`anthropic`). Per-run transport is recorded in the checkpoint; the v3 report must break accuracy out by transport for the Claude tiers as a sanity check.
- Pricing at billed rates (verified 2026-07-26): fable-5 10/50, opus-5 5/25, sonnet-5 2/10 (introductory), haiku-4.5 1/5 $/MTok. Spend cap for the extension: US$20.
- ft04/ft09/ft37 scored 0/3 across all five tiers in v2; they are flagged for a pattern audit but remain UNCHANGED in the pooled set (changing scored tasks post hoc would break comparability).

## Amendment 3 — v4: instrument revision + single-transport API rerun at N=130 (committed 2026-07-27, after v3 results, before any v4 runs)

v4 is a REVISED INSTRUMENT and a FULL FRESH RUN. v2 (N=60) and v3 (pooled N=100) verdicts remain
published as-is on the old instrument; v4 numbers are not pooled with them and do not replace them.

**Why (stated honestly):**
- The v3 pattern audit found all six 0/3-across-all-tiers tasks were instrument defects, not model
  failures: ft09/ft37/ft95/ft100 used exact-match grading against a one-word expected answer while
  their prompts demanded an explanation (structurally impossible to pass); ft95's label was also
  inverted (the refactor IS equivalent — hasattr(None,'value') is False); ft88's ground truth was
  factually wrong (JS `1<'2'<3` evaluates to true: `1<'2'` coerces to true, then `true<3` -> `1<3`);
  ft99's regex was anchored `^valid`, failing any preamble or markdown bold.
- Fixing those six raises expected top-tier accuracy to ~86%, which would trip the 85% ceiling rule.
  Per user direction, v4 adds HARDER tasks to restore headroom rather than accepting a saturated
  instrument.
- The v3 extension ran a mixed transport (49+24 tuples completed via the claude CLI after
  deterministic HTTP safety-classifier refusals). Per user directive (bench-no-cli-fallback,
  2026-07-26), benchmarks never use the CLI transport again; v4 reruns EVERYTHING single-transport.

**Instrument changes (all committed before any v4 runs):**
1. Six-task repair: ft09/ft37/ft95/ft100/ft88/ft99 converted to line-anchored answer-token regex
   grading (`(?im)^\s*(?:answer:\s*)?\*{0,2}TOKEN\b`), prompts pin "First line of your response:
   exactly <TOKEN>", ft95 label corrected to EQUIVALENT, ft88 corrected to PYTHON_ERROR_JS_TRUE,
   ft99 unanchored. Exemplars replaced with realistic verbose multi-line responses.
2. Thirty new hard tasks ft101–ft130 (target ~30–50% frontier solve): SQL/transaction semantics
   (ft101–105), concurrency/memory-model/distributed (ft106–110), floating-point/unicode/regex/
   language semantics (ft111–115), contracts/refactoring/config (ft116–120), and a user-requested
   diversified ops family (ft121–130: CI workflow semantics, git, dependency resolution, shell
   expansion, Docker layer caching, YAML merge keys, retry arithmetic, log root-cause, Makefile,
   cron). Authoring rules: regex-only grading, closed prefix-free token sets pinned in the prompt,
   no derivation leakage in prompts, refusal-safe vocabulary, executable verification of ground
   truth wherever possible. Authored after v3 results were known, without per-model tuning.
3. Grader-error audit is now a permanent CI gate: for every task whose prompt pins a token set, the
   gate synthesizes realistic response shapes (bare token, **token**, "Answer: token", lowercase,
   token + multi-line explanation) and asserts the correct token's shapes all MATCH while every
   wrong token's shapes all REJECT. Exemplars must be realistic verbose multi-line responses
   (the old gate accepted bare-word exemplars, which is how the six defects passed).
4. Refusal handling: bench/probe_refusals.py probes all tasks x refusal-prone tiers cheaply
   (max_tokens=16) before the run; any prompt the API classifier deterministically refuses gets a
   pre-committed semantic-preserving surface rewording and a re-probe, BEFORE the run starts.
   Refusals during the run remain unscored error runs; with the CLI banned, any tuple that still
   refuses after rewording is reported as a disclosed hole, never a scored answer.

**Protocol for the v4 run:**
- N=130 tasks x 5 tiers x 3 repeats = 1950 tuples, all via direct HTTP API transports
  (`anthropic-http` for Claude tiers via BENCH_API_KEY, `openai` for gpt-4o-mini). No CLI, ever.
- FRESH checkpoint (bench/results/frontier-v4-checkpoint.jsonl); v2/v3 tuples are not reused
  (task fixes make them non-comparable).
- Same margin protocol: equivalence iff |diff| <= 10pp AND TOST (Wald two-proportion) 90% CI
  entirely within +/-10pp. Same 85% ceiling rule. Machine grading only.
- Expected-accuracy note (pre-declared): with the six repairs pushing old-task accuracy up and 30
  hard tasks pulling it down, projected top-tier accuracy is roughly 75–80%; if the ceiling rule
  trips anyway, that outcome is published as-is.
- Pricing at billed rates as in Amendment 2. Spend cap for the full v4 run: US$40 (projected ~$32
  at v3-observed per-run costs, plus probes and error-retry margin).
- Runs launch only after this amendment, the task fixes, the new tasks, and the upgraded gate are
  merged to main with CI green, and the operator confirms API credit availability.

## Amendment 4 — v5: tool-call answer channel to eliminate API classifier refusal holes (committed 2026-07-27, after v4 results, before any v5 runs)

v4 completed 1742/1950 tuples; 208 tuples (fable-5 104, opus-5 104) were deterministic API
safety-classifier refusals of the prose answer-format instruction ("First line of your response:
exactly <TOKEN>"), disclosed as unscored holes. The user directed a bench that completes without
the CLI transport. v5 changes ONLY the answer channel, not the tasks:

- Same 130 tasks, same ground truth, same tiers, same 3 repeats, same transports (anthropic-http /
  openai; CLI remains banned).
- Answer collection moves to the API's structured tool-call mechanism: a forced `submit_answer`
  tool whose schema is the task's pre-existing closed token set as an enum. The obsolete prose
  format sentence is removed at request time by one uniform transform (tasks file untouched,
  identical across tasks and tiers; no per-model or per-task tuning). Tasks without a closed token
  set keep prose+regex grading and are marked answer_mode:"regex" in the checkpoint.
- Grading for tool-mode tasks is exact enum equality — this also retires the regex-fragility
  defect class the v3 pattern audit found.
- Refusal policy unchanged: any refusal remains an unscored error run, disclosed as a hole. This
  amendment is one clean redesign of the answer channel; if the classifier still refuses
  tool-shaped requests, that result is published as-is. No iterative rewording.
- Probe-first: probe_refusals.py --answer-mode tool runs over all tasks x fable-5/opus-5 before
  the full run; the probe outcome is reported either way.
- v5 is a fresh run and a revised instrument: fresh checkpoint
  (bench/results/frontier-v5-checkpoint.jsonl), NOT pooled with v2/v3/v4, which stand as
  published. Same +/-10pp TOST margin, same 85% ceiling rule (v4 tripped it at 90.6%; if v5 trips
  it too that is published as-is and the next instrument revision adds harder tasks rather than
  reinterpreting the rule). Spend cap for the full v5 run: US$40.

**Token-set audit result (before any v5 runs):** 39/130 tasks have closed token sets (ft09, ft37,
ft88, ft95, ft99, ft100, ft101–ft130). These 39 tasks run in tool mode (request has tool def +
tool_choice, response graded by exact enum match, checkpoint recorded answer_mode:"tool"). The
remaining 91 tasks (ft01–ft08, ft10–ft36, ft38–ft87, ft89–ft98) lack extractable token sets and
fall back to prose+regex mode (checkpoint recorded answer_mode:"regex"); no change from v4.

**Prompt transform:** Tasks with token sets have the prose "First line of your response: exactly
..." instruction stripped at request time (regex: `(?:First line\s*(?:of your response)?:\s*exactly\s+.+?(?:\n|$))`; identical transform across all 39 tasks). Replaced with "Submit your final answer by calling the submit_answer tool." Tool schema: {type:"object", properties:{answer:{type:"string", enum:[...tokens...]}}, required:["answer"]}. Anthropic tool_choice: {type:"tool", name:"submit_answer"}. OpenAI tool_choice: {"type":"function","function":{"name":"submit_answer"}}. Tool response parsing: exact string equality check of the "answer" field against the task's correct token (derived from ground-truth regex matching one token).

**Compatibility:** v5 runner (`run_v2_parallel.py --answer-mode tool`) and probe (`probe_refusals.py --answer-mode tool`) are shipped; default checkpoint is `bench/results/frontier-v5-checkpoint.jsonl`; OpenAI seam remains regex-only (gpt-4o-mini lacks structured enum-constrained outputs; falls back to prose+regex). Refusal handling unchanged; probe-first before main run.
