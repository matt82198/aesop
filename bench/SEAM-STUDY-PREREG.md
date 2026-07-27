# SEAM Study Pre-registration

## Study Design

S-arm (seated dispatcher) for seam-discrimination study via AgentDriver.

## Addendum 1 — uniform repair budget (2026-07-27, before any runs)

The S arm applies a **uniform repair budget** to every tier: 1 initial attempt + up to 2 visible-test-driven repair attempts (3 model calls max per run), overriding per-tier driver policy caps, which are recorded but not applied.

**Rationale**: Ensure fair comparison across tiers. Arms (S vs U) differ only by seat (API vs Claude Code); tiers (Tier 1 vs 2) differ only by backend accuracy. Treatment must be uniform within arms to isolate the seat effect.

**Implementation**:
- CLI flag: `--repair-cap N` (default: 2, meaning 1 initial + 2 repairs = 3 total attempts max)
- Checkpoint records: both `policy_repair_cap` (driver's recommendation) and `applied_repair_cap` (CLI value applied)
- Loop semantics: `total_attempts = 1 + applied_repair_cap`
- `retries_used`: counts actual repairs that happened (0 = first attempt succeeded, max = `applied_repair_cap`)

**Transparency**: Policy-recommended caps still recorded for analysis; readers can see per-tier recommendations vs applied uniform treatment.
