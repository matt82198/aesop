# BUILDLOG — aesop 0.4.0 cycle

Append-only checkpoint snapshots for each wave.

## 0.4.0 Cycle Checkpoint (2026-07-25)

**Summary**: 0.4.0 staged on main@cf7fdbb; version + CHANGELOG prepared; publish user-gated.

**HS-0 Refinement Hardening** — Preliminary audit + schema/seam fixes (PRs #371–#372)
- Round-1: 6 lenses (analyst + adversarial + audit-delta); 19 findings categorized
- Round-2: 12 lenses; IPv6/DNS SSRF hardening, worker-seat redaction depth, bench stability, docs edits

**HS-1 Unified Two-Seat Config** (2 audit rounds, PR #378)
- New `seats` config block swaps worker AND orchestrator models from single config
- Legacy flat config no-op default; install-time safety
- Round-1: CLAUDE.md overflow gate, api_key_env allowlist, DNS resolution blocking, is_local loopback pin
- Round-2: promotion parity, node_id uniqueness, FakeOrchestratorBackend canning

**HS-2 Live Orchestrator-Seat Swap + Block-Gate Hardening** (2 audit rounds, PR #379 + F4 quarantine)
- final_catch gate: model swap mid-flight without restarting fleet; crash-only degradation
- Block-gate hardening (2 rounds): JSON-boundary validation, malformed-message fail-closed, evidence injection guard
- F4 quarantine fix: discovered+fixed incident-response path leaking context on crash

**HS-3 MICROKERNEL Docs** (PR #380)
- New docs/MICROKERNEL.md: proof-of-concept model-swap seam, multi-model verification bounds, 60s quickstart

**HS-4 Release Preparation** (PR #381)
- 0.4.0 version bump + CHANGELOG (v0.4.0 ships two-seat config, IPv6/DNS hardening, driver/ scaffolding, live orchestrator swap)
- main green: cf7fdbb, all required CI passing
- CLI driver/ scaffolding completeness fix (npx aesop init now emits driver/ tree)
- Bench: frontier slice results + HS-2 swap proof (bench/results/hs2-swap-proof-2026-07-25.*)

**Items Marked DONE (tracked/wave-31-close-reconciliation)**
- 0c75681341ea: shared merge-wait helper (wave-13)
- 222bab448f40: stall-detection watchdog (wave-13)
- 0e7cc4709e42: inc 1.6 schema reconciliation (PR #357+)
- b25068117995: inc 4a seated shadow adjudication (PR #358)
- 67b20009898a: ci_merge_wait fail-closed exit codes (PR #376)
- a00c762dc95c: ci_merge_wait --expect-checks semantic fix (PR #376)
- 79e40e7fecb4: windows-shard max-parallel contention hardening (PR #376)

**Confound Found & Fixed**: item-9 context-leak on answer-leak + mechanism=refutation (inc 2.5 shadow ladder). Reconciled in inc 2.6 corpus methodology; seated A/B (inc 4a) reverified with real context, item 9 flipped true both models.

**Open Parked Items** (not blocking release)
- a16eac67f7de: ps1-syntax CI gate
- fb142031d1dc: install-tasks audit log
- d1c69aed37f9: inc 2.6 broader corpus
- f84f587573fc: driver/CLAUDE.md restructure decision
- 3f7c9a2e8b14: test_frontier_slice test-pollution (NEW)

**Next**: Wave-31 backlog continuation (WS3b failure-recovery, StateAPI burndown, frontier live-run spend gate, external benchmark slice).
