# Benchmark V3 run — 2026-07-29 — Haiku vs Sonnet vs Opus (API)

The v3 (28 judgment tasks), run via Anthropic API (BENCH_API_KEY).

## Method

Each model answered all 28 tasks **blind** (no access to ground truth), scored by exact/regex match.
Runs via direct HTTP API, not CLI.

## Result

| Model  | Score | Accuracy | Avg Tokens | Total Tokens |
|--------|-------|----------|-----------|--------------|
| Opus   | 25/28 | 89%      | 52.92857142857143 | 1482         |

## Cost axis

Total tokens across all 28 tasks:
- **Opus**: 1482 tokens

## Notes
- Runs via Anthropic API (BENCH_API_KEY), not CLI
- Transport: anthropic-http
