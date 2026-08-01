# STATE — Durable System Checkpoint

**What this file is:** The live durable checkpoint that Aesop itself uses during its own `/buildsystem` loop. It records the current system version, architectural decisions, known limitations, and the next milestone. This is not historical archive; it is read by the orchestrator to understand operational state.

**Current Version:** v0.7.2 (tagged 2026-07-31). 0.7.1 was a hardening release: 12 PRs against gates that reported success without verifying anything, plus the portability work needed for the remote-access features to run outside a single machine (fleet state and remote-command identity are now configuration, not an assumed home layout). 0.7.2 adds one fix on top: `/api/state` served the collector's empty default snapshot instead of computing the section inline, so the dashboard's first paint could show an empty data section.

## Architectural Thesis

Agent behavior is source code. Rules, memory, hooks, and checkpoints live as versioned, portable, diffable filesystem artifacts in git. This design enables code-review, versioning, and forensic replay of orchestration itself—not just the agents' output.

A corollary the 0.7.0 guardrail work makes explicit: a rule written only as prose is not enforced. Every operating rule that matters is expected to become a hook, gate, or linter that fails closed.

## Known Limitations

- **Multi-instance coordination is single-box only.** The 0.7.0 MVP added lease-based SQLite claims (with split-brain and TOCTOU fixes), but claims remain file-system-backed. Multi-box deployment requires a shared lease service; not yet implemented.
- **State-layer consolidation in flight** (git + SQLite + STATE.md are currently three sources of truth; scheduled to collapse into SQLite-as-source + git-as-audit-trail).
- **Benchmark is curated, not sampled** (N=39 judgment tasks, not real-fleet transcripts). Sufficiency is proven; equivalence-to-Opus is not claimed.
- **Documentation gates verify presence, not truth.** A gate that requires a doc to exist induces agents to write one; green means "a doc exists", not "the doc is accurate". Doc claims still need reading against the code.

## Next Milestone

**Wave-31+:** State-layer multi-instance lifecycle (crash-orphan recovery, liveness detection, monotonic expiry); unsupervised failure-recovery loop; frontier live-run capability; external-benchmark validation.

**Post-0.7.0 cleanup (in flight):** dependency manifests (`requirements.txt` / `requirements-dev.txt`); reconciliation of three contradictory test-suite counts onto one script with a fail-closed gate; `_run_wave_inner` phase decomposition; README reduction from ~21 KB to ~6 KB; `tools/` CLI-base extraction to retire per-script boilerplate.
