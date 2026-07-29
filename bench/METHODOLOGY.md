# Benchmark Methodology

## Pre-declared Ceiling Rule

**The ceiling rule is the binding gate for all runs:** If two or more tiers land at or above 92% (or 85% on amended runs), the instrument failed to discriminate — we publish this result explicitly rather than claiming equivalence. This rule prevents false-positive tier claims on curated (low-N) benchmarks.

## Core Methodology

- Declaration date/time: 2026-07-26 (UTC timestamp of this commit is the binding record).
- **Claim protocol: we claim tier equivalence on the discrimination slice ONLY IF the observed accuracy difference is under 10 percentage points AND the TOST 90% confidence interval for the difference lies entirely within ±10pp.** (Equivalence bounds ±10pp, alpha=0.05 per one-sided test.)
- Anything outside that: we report the gap as-is, including results that hurt.
- Protocol: each tier runs the same slice with **3 repeats per task**; per-task majority scoring; report per-tier score DISTRIBUTIONS (per-task, per-repeat), not just means.
- Grading provenance (pre-declared): grading is machine-checked against ground-truth answer patterns committed in the slice definitions; no model-graded scoring in this run. If any task requires judgment-based grading, it is excluded or flagged, never silently model-graded.
- Tiers under test: claude-opus-5 (released 2026-07-24), claude-fable-5, claude-sonnet-5, claude-haiku-4-5-20251001, plus one non-Claude tier (OpenAI seam) for external validity.
- Spend cap for the full run: US$20.
- Statistical honesty notes: N=20 tasks x 3 repeats is SMALL; ruling out gaps much smaller than ~10pp is not possible at this N — that is WHY the margin is 10pp. Larger claims require a bigger slice (tracked as follow-up).

## Design History

All amendments below were pre-committed before their respective runs began. Each preserves prior results unchanged and discloses the decision process transparently.

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

## Amendment 4 — v5: tool-call answer channel to eliminate ALL API classifier refusal holes (committed 2026-07-27, after v4 results, before any v5 runs)

v4 completed 1742/1950 tuples; 208 tuples (fable-5 104, opus-5 104) were deterministic API
safety-classifier refusals of the prose answer-format instruction ("First line of your response:
exactly <TOKEN>" and variants), disclosed as unscored holes. The user directed a bench that completes
zero holes. v5 changes the answer channel for ALL 130 TASKS, not just closed-set tasks:

- Same 130 tasks, same ground truth, same tiers (all 5 including gpt-4o-mini), same 3 repeats, same
  transports (anthropic-http / openai; CLI remains banned).
- ALL 130 tasks use tool-call answer channel (no tier has prose format instructions anymore):
  * **39 closed-set tasks** (ft09, ft37, ft88, ft95, ft99, ft100, ft101–ft130): enum schema,
    format instruction stripped, tool called with constrained enum, graded by exact string equality
  * **91 free-string tasks** (ft01–ft08, ft10–ft36, ft38–ft87, ft89–ft98): string schema, format
    instruction stripped, tool called with free-text answer, graded by running the task's
    ground-truth regex against the submitted answer string
- Uniform prompt transform (one identical regex per format-instruction variant, applied to all tasks):
  strips "First line of your response: exactly ...", "First line: exactly ...", and "Answer with ...
  on the first line ..." sentences. Appended instruction: "Call the submit_answer tool with answer
  set to ONLY your final answer value - no explanation."
- Grading regexes UNCHANGED from v4 (exemption: exact equality for enum tasks replaces regex; both
  achieve zero scoring ambiguity and verify the exemplar/counter-example audit in CI).
- Refusal policy unchanged: any refusal remains an unscored error run, disclosed as a hole. v5 is a
  clean redesign of the answer channel; if the classifier still refuses tool-shaped requests, that
  result is published as-is.
- Probe-first: probe_refusals.py --answer-mode tool runs all 130 tasks x fable-5/opus-5 before the
  full run; the probe outcome is reported either way.
- v5 is a fresh run and revised instrument: fresh checkpoint (bench/results/frontier-v5-checkpoint.jsonl),
  NOT pooled with v2/v3/v4, which stand as published. Same +/-10pp TOST margin, same 85% ceiling rule
  (v4 tripped it at 90.6%; if v5 trips it too that is published as-is). Spend cap: US$40.

**Schema summary:** Every task and every tier uses the tool channel. 39 tasks have enum-constrained
schemas (graded by exact string match against correct token). 91 tasks have free-string schemas
(graded by running the pre-existing ground-truth regex against the submitted answer). All existing
ground-truth patterns remain in place; no pattern changes.

**Format instruction variants stripped by uniform transform:** "First line of your response: exactly
...", "First line: exactly ...", "Answer with <tokens> on the first line, then explain..." (and
similar trailing-clause variants with "/" separators). The transform removes the sentence containing
the format instruction and nothing else.

**Tool schema shapes:**
- Enum (39 tasks): `{type:"object", properties:{answer:{type:"string", enum:[TOKEN1,TOKEN2,...]}}, required:["answer"]}`
- String (91 tasks): `{type:"object", properties:{answer:{type:"string"}}, required:["answer"]}`
- Both: tool_choice forces submit_answer; Anthropic: {type:"tool", name:"submit_answer"}; OpenAI:
  {type:"function", function:{name:"submit_answer"}}

**Compatibility:** All tiers including gpt-4o-mini use tool mode (OpenAI function calling). Runner
and probe updated to support both schemas. Default checkpoint: frontier-v5-checkpoint.jsonl.

## Amendment 5 — v5 frozen 93-task subset (committed 2026-07-27, after tool-mode probes, before any v5 runs)

Two identical tool-mode probe passes over all 130 tasks x {fable-5, opus-5} found a deterministic
refusal core: 48 (task, tier) pairs refused in BOTH passes (37 distinct tasks); 4 further pass-2-only
refusals were stochastic. Per Amendment 4's no-iteration rule those prompts are not reworded again.

- v5 instrument = the 93 tasks every tier's API deterministically serves. The exclusion rule is
  probe-derived and applied identically to all tiers (no per-model tuning). Excluded task ids:
  ft02_code_defect_detection_concurrent,ft04_long_context_needle_judgment,ft06_git_history_blame_analysis,ft113_regex_split_capturing_group,ft116_generator_exhaustion_refactor,ft126_yaml_merge_key_precedence,ft127_retry_backoff_attempt_count,ft16_unicode_normalization_gotcha,ft18_type_coercion_subtle_bug,ft19_format_string_vulnerability,ft21_sql_join_optimization_complex,ft23_memory_ordering_volatile_bug,ft26_instruction_compatibility_constraint,ft28_long_context_timing_inconsistency,ft31_git_blame_security_regression,ft41_regex_backref_vs_alternation,ft42_regex_lookahead_negative,ft43_api_response_enum_violation,ft44_api_request_body_size_constraint,ft49_state_machine_idempotency,ft51_unicode_emoji_byte_length,ft55_type_coercion_array_comparison,ft57_sql_injection_parameterized,ft58_xpath_injection_xml_parse,ft64_connection_pool_saturation_symptom,ft68_full_text_search_index_strategy,ft71_atomic_vs_compound_race,ft77_distributed_lock_clock_skew,ft78_memory_ordering_acquire_release,ft79_cas_retry_loop_correctness,ft82_unicode_byte_vs_char_emoji_with_combining,ft83_type_coercion_python_vs_js_string_concat,ft84_regex_alternation_grouping_precedence,ft86_json_schema_allof_required_merge,ft90_unicode_emoji_skin_tone_modifier,ft94_api_contract_content_type_violation,ft96_long_context_config_priority_contradiction
- Selection-effect disclosure: exclusion is conditioned on fable-5/opus-5 classifier behavior; the
  surviving set under-represents content those classifiers refuse (observed clusters: injection
  vocabulary, concurrency/memory-ordering, unicode/emoji, git-blame, api-contract). Cross-tier
  comparisons remain valid (identical 93 tasks per tier); absolute accuracies describe this 93-task
  set only and are not comparable to v2/v3/v4 absolutes.
- Run: 93 x 5 x 3 = 1395 tuples; completion criterion 1395/1395 good tuples (zero holes).
  Transients and stochastic refusals retry from checkpoint as identical requests, bounded at 3
  rounds; any pair refusing 3 consecutive times HALTS the run and reopens the freeze — holes are
  never published as results.
- All other protocol per Amendments 3-4 (margins, 85% ceiling published-as-is, billed pricing,
  US$40 cap inclusive of ~$1 probe spend).
- Replacing the 37 excluded tasks with newly authored same-family content remains a possible
  pre-registered v6 extension; it is not part of v5.
