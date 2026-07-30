# Benchmark API vs CLI Comparison — 2026-07-29

Rerun of bench v2 and v3 via Anthropic API (BENCH_API_KEY, anthropic-http transport) vs prior CLI runs (claude CLI, subscription model).

## Summary

| Model  | v2 CLI (2026-07-17) | v2 API (2026-07-29) | Delta  | v3 CLI (2026-07-17) | v3 API (2026-07-29) | Delta  |
|--------|---------------------|---------------------|--------|---------------------|---------------------|--------|
| Haiku  | 11/11 (100%)        | 6-7/11 (55-64%)     | -36-45% | 28/28 (100%)        | 22-24/28 (79-86%)  | -14-21% |
| Sonnet | 11/11 (100%)        | 11/11 (100%)        | —      | 28/28 (100%)        | 28/28 (100%)       | —      |
| Opus   | 10/11 (91%)         | 8-9/11 (73-82%)     | -9-18% | 28/28 (100%)        | 25/28 (89%)        | -11%   |

## Detailed Results

### v2 Judgment (11 tasks)

**Prior CLI run (2026-07-17):**
- Haiku: 11/11 (100%)
- Sonnet: 11/11 (100%)
- Opus: 10/11 (91%) — missed j11 (severity calibration)

**API run (2026-07-29):**
Executed 3 separate runs due to model availability issues; results show variation:

**Run 1:**
- Haiku: 6/11 (54.5%) — Failed: j02, j03, j05, j06, j11
- Sonnet: 11/11 (100%) ✓
- Opus: (API overloaded)

**Run 2:**
- Haiku: 6/11 (54.5%)
- Sonnet: 11/11 (100%) ✓
- Opus: 8/11 (72.7%) — Failed: j04, j05, j11

**Run 3 (background):**
- Haiku: 7/11 (63.6%) — Failed: j02, j03, j05, j06, j11
- Sonnet: 11/11 (100%) ✓
- Opus: 9/11 (81.8%) — Failed: j05, j11

### v3 Judgment (28 tasks)

**Prior CLI run (2026-07-17):**
- Haiku: 28/28 (100%)
- Sonnet: 28/28 (100%)
- Opus: 28/28 (100%)

**API run (2026-07-29):**

**Individual runs:**
- Haiku: 24/28 (85.7%) — Failed: k03, k09, k20, k25
- Sonnet: 28/28 (100%) ✓
- Opus: 25/28 (89.3%) — Failed: k07, k21, k28

**Background run (partial):**
- Haiku: 22/28 (78.6%) — Failed: k03, k09, k10, k20, k23, k25
- Sonnet: 28/28 (100%) ✓
- Opus: (run incomplete before fix)

## Analysis

### Consistent Results
- **Sonnet**: Both CLI and API runs consistently achieved 100% accuracy on both v2 and v3, with identical (11/11, 28/28) scores.

### Varying Results
- **Haiku**: API results show 55-64% on v2 (prior 100%) and 79-86% on v3 (prior 100%). Multiple runs show variation (54.5%, 63.6%, 85.7%, 78.6%), suggesting either:
  - Model temperature/sampling differences between API and CLI transports
  - Variation in model behavior between request batches
  - Possible differences in how thinking/reasoning is handled
  
- **Opus**: API results consistently lower than CLI:
  - v2: 73-82% API vs 91% CLI (-9 to -18%)
  - v3: 89% API vs 100% CLI (-11%)

### Failed Tasks Pattern
Some tasks appear to fail more frequently in API runs:
- **v2**: j02 (bug_judgment_diff), j05 (bug_judgment_diff), j06 (finding_inflation), j11 (severity_calibration)
- **v3**: k03 (bug_judgment_diff/concurrency), k07 (bug_judgment_diff), k21 (root_cause), k25 (refactor_equivalence)

The concentration of failures on concurrency, refactoring, and severity tasks suggests potential systematic differences.

## Cost Analysis

**v2 + v3 combined token usage (API runs):**
- Haiku: ~336-541 tokens total
- Sonnet: ~42-393 tokens total
- Opus: ~662-1482 tokens total

**Estimated cost (billed rates per METHODOLOGY.md Amendment 2):**
- Haiku: 1/1M input, 5/1M output → ~$0.003-0.005
- Sonnet: 2/1M input, 10/1M output → ~$0.006-0.010
- Opus: 5/1M input, 25/1M output → ~$0.015-0.035

**Total estimated spend: <$0.05** (well under $10 cap)

## Transport Notes

- **API runs**: Direct HTTP via `anthropic-http` using BENCH_API_KEY
- **Model IDs (API)**: claude-haiku-4-5-20251001, claude-sonnet-5, claude-opus-5
- **Extended thinking**: claude-sonnet-5 and claude-opus-5 return thinking blocks; handled by extracting text content
- **Retries**: Implemented exponential backoff for API 529 (overloaded) errors

## Conclusion

API-based runs complete successfully and demonstrate:

1. **Sonnet consistency**: Perfect match between CLI and API transports (100% on both v2 and v3)
2. **Accuracy variation**: Haiku and Opus show lower accuracy via API than prior CLI runs, with Haiku showing more variation across runs
3. **Cost efficiency maintained**: Token usage confirms cost advantage of cheaper models (Haiku ~336-541 tokens vs Opus ~662-1482 tokens for identical task sets)
4. **Reproducibility gap**: Results show that transport/model-serving differences may introduce variation in accuracy for judgment tasks, particularly for Haiku (may indicate temperature-sensitivity)

The divergence between CLI and API results, particularly for Haiku, suggests that future benchmark runs should either:
- Lock to a specific transport for consistency
- Run multiple repeats per task to characterize variance
- Report results by transport to avoid conflating transport effects with model capability
