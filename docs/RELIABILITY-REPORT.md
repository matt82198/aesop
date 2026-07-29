# Chaos-Wave Resilience Report

## Fault Injection & Recovery Analysis

### Taxonomy Table

| Fault Class | Name | Detection Mechanism | Detection (s) | Recovery Path | MTTR (s) | Verdict |
|---|---|---|---|---|---|---|
| F1 | Worker Termination | Journal stale check (in-progress flag) | 0.0 | Crash-only start from journal; re-dispat | 0.0 | PASS |
| F2 | Checkpoint Corruption | JSON parse error on corrupted line | 0.001 | Skip corrupted entry, resume from valid  | 0.001 | PASS |
| F3 | Secret Planted | Regex pattern match (OpenAI-style sk- to | 0.001 | Secret-scan pre-push gate BLOCKS; requir | 0.0 | PASS |
| F4 | Heartbeat Stall | Heartbeat age check (now - timestamp >=  | 0.0 | Watchdog signals stale worker; orchestra | 0.5 | PASS |
| F5 | Red Test | Exact gate: test exit code != 0 | 0.041 | Merge gate refuses; test output sent to  | 0.141 | PASS |

## Summary
- **Total Faults**: 5
- **Passed**: 5
- **Failed**: 0
- **Errors**: 0
- **Success Rate**: 100%

## Test Command

```bash
python tools/chaos_harness.py --offline
```

## Reproducibility

All measurements are deterministic and data-derived (no synthetic delays).
Fault injection uses controlled sandbox isolation to prevent real-repo damage.
Recovery paths mirror the crash-only start protocol used in production.