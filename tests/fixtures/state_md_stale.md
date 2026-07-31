# STATE — Durable System Checkpoint

**What this file is:** The live durable checkpoint that Aesop itself uses during its own `/buildsystem` loop.

**Current Version:** v0.5.0 (released 2026-07-29, live on npm latest).

## Architectural Thesis

Agent behavior is source code. Rules, memory, hooks, and checkpoints live as versioned, portable, diffable filesystem artifacts in git.

## Known Limitations

- **Multi-instance coordination is single-box only** (SQLite WAL, file-system-backed claim key).
- **State-layer consolidation in flight** (git + SQLite + STATE.md are currently three sources of truth).
- **Benchmark is curated, not sampled** (N=39 judgment tasks, not real-fleet transcripts).

## Next Milestone

**Wave-31+:** State-layer multi-instance lifecycle.
