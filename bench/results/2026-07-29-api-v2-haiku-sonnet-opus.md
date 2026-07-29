# Benchmark V2 run — 2026-07-29 — Haiku vs Sonnet vs Opus (API)

The v2 (11 judgment tasks), run via Anthropic API (BENCH_API_KEY).

## Method

Each model answered all 11 tasks **blind** (no access to ground truth), scored by exact/regex match.
Runs via direct HTTP API, not CLI.

## Result

| Model  | Score | Accuracy | Avg Tokens | Total Tokens |
|--------|-------|----------|-----------|--------------|
| Opus   | 8/11  | 73%      | 66.0      | 726          |

## Cost axis

Total tokens across all 11 tasks:
- **Opus**: 726 tokens

## Notes
- Runs via Anthropic API (BENCH_API_KEY), not CLI
- Transport: anthropic-http
