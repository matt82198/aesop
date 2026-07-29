# Wave Latency Report
| Wave | Wall-Clock (s) | Items | Mean Item (s) | P50 (s) | P95 (s) | Orch Overhead (s) | Method |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bench-fake-transport | 0.0 | 0 | N/A | N/A | N/A | 0.0 | wall_clock_minus_parallel |
| bench-gpt-4o-mini | 0.0 | 0 | N/A | N/A | N/A | 0.0 | wall_clock_minus_parallel |
| bench-unknown | 0.0 | 0 | N/A | N/A | N/A | 0.0 | wall_clock_minus_parallel |
| claude-fable-5-long | 47.7 | 24 | 16.7 | 14.7 | 42.0 | 0.0 | wall_clock_minus_parallel |
| claude-fable-5-medium | 16.9 | 24 | 9.6 | 9.2 | 16.0 | 0.0 | wall_clock_minus_parallel |
| claude-fable-5-short | 26.7 | 24 | 11.9 | 9.5 | 26.1 | 0.0 | wall_clock_minus_parallel |
| claude-haiku-4-5-20251001-long | 28.5 | 24 | 8.8 | 6.6 | 25.7 | 0.0 | wall_clock_minus_parallel |
| claude-haiku-4-5-20251001-medium | 10.5 | 24 | 4.0 | 3.6 | 9.1 | 0.0 | wall_clock_minus_parallel |
| claude-haiku-4-5-20251001-short | 7.7 | 24 | 4.1 | 3.7 | 7.7 | 0.0 | wall_clock_minus_parallel |
| claude-opus-5-long | 63.1 | 24 | 33.4 | 30.3 | 62.8 | 0.0 | wall_clock_minus_parallel |
| claude-opus-5-medium | 20.5 | 24 | 12.2 | 10.5 | 20.4 | 0.0 | wall_clock_minus_parallel |
| claude-opus-5-short | 15.9 | 24 | 9.8 | 8.2 | 15.7 | 0.0 | wall_clock_minus_parallel |
| claude-sonnet-5-long | 36.1 | 24 | 16.3 | 15.8 | 33.9 | 0.0 | wall_clock_minus_parallel |
| claude-sonnet-5-medium | 13.4 | 24 | 8.0 | 7.4 | 13.0 | 0.0 | wall_clock_minus_parallel |
| claude-sonnet-5-short | 23.6 | 24 | 7.6 | 7.1 | 19.9 | 0.0 | wall_clock_minus_parallel |
| gpt-4o-mini-long | 74.0 | 24 | 18.6 | 13.0 | 68.3 | 0.0 | wall_clock_minus_parallel |
| gpt-4o-mini-medium | 20.5 | 24 | 6.8 | 6.7 | 19.1 | 0.0 | wall_clock_minus_parallel |
| gpt-4o-mini-short | 25.6 | 24 | 6.6 | 5.1 | 25.0 | 0.0 | wall_clock_minus_parallel |

## Methodology

**Orchestrator Overhead Estimation**: `overhead = wall_clock_s - max(item_durations_s)`, assuming perfect parallelism.

This estimates the orchestrator's non-work time (dispatch, coordination, repair loop overhead, etc.). Negative values indicate noisy measurements where item durations exceed the measured wave wall-clock (typically from incomplete instrumentation or concurrent background work).

**Caveats**:
- Durations sourced from committed results/journals (bench results, wave journals)
- Missing timing data reported as N/A (not estimated)
- Percentiles (p50, p95) calculated from available item samples
- Method assumes homogeneous agent work (parallelism model is simplified)
