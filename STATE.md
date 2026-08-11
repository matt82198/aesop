# STATE — Durable System Checkpoint

**What this file is:** The live durable checkpoint that Aesop itself uses during its own `/buildsystem` loop. It records the current system version, architectural decisions, known limitations, and the next milestone. This is not historical archive; it is read by the orchestrator to understand operational state.

**Current Version:** v0.7.2 (tagged 2026-07-31, current HEAD 620294cd 2026-08-02). 0.7.1 was a hardening release: 12 PRs against gates that reported success without verifying anything, plus the portability work needed for the remote-access features to run outside a single machine (fleet state and remote-command identity are now configuration, not an assumed home layout). 0.7.2 adds one fix on top: `/api/state` served the collector's empty default snapshot instead of computing the section inline, so the dashboard's first paint could show an empty data section. Post-tag (2026-08-02): 6 merges (#671–675, #678–679) harden gate activation verification, update documentation to reflect 266 tests (vs. 254 in 0.7.1), remove dead scripts, and wire gate-runability enforcement (in-flight: #676 cost-drawer UI, #677 gate-runability hardening).

## Architectural Thesis

Agent behavior is source code. Rules, memory, hooks, and checkpoints live as versioned, portable, diffable filesystem artifacts in git. This design enables code-review, versioning, and forensic replay of orchestration itself—not just the agents' output.

A corollary the 0.7.0 guardrail work makes explicit: a rule written only as prose is not enforced. Every operating rule that matters is expected to become a hook, gate, or linter that fails closed.

The 0.7.1 release added a second corollary: a gate that exists is not a gate that ran. The portability gate sat behind a failing lint step in the same CI job and had never produced a verdict; fixing the earlier step surfaced 9 real violations that had accumulated unseen. Gate *activation* is now its own thing to verify, separately from gate correctness.

## Known Limitations

- **Multi-instance coordination is single-box only.** The 0.7.0 MVP added lease-based SQLite claims (with split-brain and TOCTOU fixes), but claims remain file-system-backed. Multi-box deployment requires a shared lease service; not yet implemented.
- **State-layer consolidation in flight** (git + SQLite + STATE.md are currently three sources of truth; scheduled to collapse into SQLite-as-source + git-as-audit-trail).
- **Benchmark is curated, not sampled** (N=39 judgment tasks, not real-fleet transcripts). Sufficiency is proven; equivalence-to-Opus is not claimed.
- **`count_git_files` fail-closed as of #674.** `tools/verify_test_suite_count.py` now detects and exits non-zero if git fails; gate-derived test count is 266 (not 254; see #678 documentation update). Vacuous-green risk eliminated.
- **Documentation gates verify presence, not truth.** A gate that requires a doc to exist induces agents to write one; green means "a doc exists", not "the doc is accurate". Doc claims still need reading against the code.

## Next Milestone

**Wave-31+:** State-layer multi-instance lifecycle (crash-orphan recovery, liveness detection, monotonic expiry); unsupervised failure-recovery loop; frontier live-run capability; external-benchmark validation.

**NEXT STEPS (post-0.7.2, ranked):**

1. **Cost-summary drawer UI** (tail PR in review). Persistent cost drawer; merged tail gate-hardening
   work (#671–675, #678–679) unblocks final integration and test.
2. **Gate-runability enforcement** (tail PR in review). ci_gate_runability wired + documented-gates-are-wired
   guardrail; completes gate-activation verification story from #674–679 shard.
3. **Coverage fix for count-gate** (deliberately open, requires derivation). 
   Real coverage for count-gate fail-closed (as of #674); merging requires deriving test-count 
   expectations instead of hardcoding.
4. **`test_agent_detail_roundtrip` pollutes `test_api_state`.** `config.reload()` in `setUp`
   with no reload after the env restore in `tearDown` was fixed in #668, but the shard-level
   failure had a second cause (the `/api/state` empty-default bug, also fixed in 0.7.2). Re-verify the
   pair stays green under `ci_shard_runner` before assuming it is fully closed.
5. **`test_hook_preflight` has never executed.** It raises a module-level `unittest.SkipTest`
   with an honest docstring; #667 made the runner report that as SKIPPED rather than an import
   failure, but the coverage gap is open. A rewrite must also fix the `tmp_path` NameError the
   pytest-style form was hiding.
6. **Flaky `test_openai_transport_redirect`** in shard 0 (pytest mode) — failed once, passed on
   a clean re-run of the same shard, passes in isolation. Characterize before it reds unrelated PRs.
7. **Carry-over cleanup:** dependency manifests (`requirements.txt` / `requirements-dev.txt`);
   `_run_wave_inner` phase decomposition; `tools/` CLI-base extraction.

**Release-state note:** `v0.7.1` is tagged at `ec5ea9db` and has **no GitHub release** — that
commit's CI was red (pre-existing `/api/state` bug). The tag was deliberately NOT moved, since
retagging a pushed release rewrites published history. `v0.7.2` (`e061f2bd`) is the first tag in
this line cut *after* main's own CI went green, and is the published Latest release. Consumer-visible
release history therefore reads 0.7.0 -> 0.7.2; publishing 0.7.1 retroactively is a user decision.
`npm publish` has NOT been run for either version and remains user-gated.
