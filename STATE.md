# STATE — Durable System Checkpoint

**What this file is:** The live durable checkpoint that Aesop itself uses during its own `/buildsystem` loop. It records the current system version, architectural decisions, known limitations, and the next milestone. This is not historical archive; it is read by the orchestrator to understand operational state.

**Current Version:** v0.8.0 (tagged + released 2026-09-11; npm latest 0.8.0). HEAD: 4837ae60 (2026-10-05); 73 commits since v0.8.0.

## Architectural Thesis

Agent behavior is source code. Rules, memory, hooks, and checkpoints live as versioned, portable, diffable filesystem artifacts in git. This design enables code-review, versioning, and forensic replay of orchestration itself—not just the agents' output.

A corollary the 0.7.0 guardrail work makes explicit: a rule written only as prose is not enforced. Every operating rule that matters is expected to become a hook, gate, or linter that fails closed.

The 0.7.1 release added a second corollary: a gate that exists is not a gate that ran. The portability gate sat behind a failing lint step in the same CI job and had never produced a verdict; fixing the earlier step surfaced 9 real violations that had accumulated unseen. Gate *activation* is now its own thing to verify, separately from gate correctness.

## Known Limitations

- **Multi-instance coordination is single-box only.** The 0.7.0 MVP added lease-based SQLite claims (with split-brain and TOCTOU fixes), but claims remain file-system-backed. Multi-box deployment requires a shared lease service; not yet implemented.
- **State-layer consolidation in flight** (git + SQLite + STATE.md are currently three sources of truth; scheduled to collapse into SQLite-as-source + git-as-audit-trail).
- **Benchmark is curated, not sampled** (N=39 judgment tasks, not real-fleet transcripts). Sufficiency is proven; equivalence-to-Opus is not claimed.
- **Documentation gates verify presence, not truth.** A gate that requires a doc to exist induces agents to write one; green means "a doc exists", not "the doc is accurate". Doc claims still need reading against the code.
- **Trigger-layer and box-restore fragility.** Orchestrator-startup recovery (scheduled tasks, conductor3 state clone) is now gated by scheduled-task execution (#787 fix pending in weekly drift PR #790). If scheduled tasks do not fire, trigger layer never runs state updates. Guardrail check proposed (power_selftest trigger-layer check).
- **STATE.md checkpoint staleness.** Live checkpoint was 110+ PRs stale (v0.7.2 era → 2026-08-18 main). Freshness gate proposed; this regeneration sets baseline (2026-08-19).

## Next Milestone

**Wave-31+:** State-layer multi-instance lifecycle (crash-orphan recovery, liveness detection, monotonic expiry); unsupervised failure-recovery loop; frontier live-run capability; external-benchmark validation.

**NEXT STEPS (post-0.7.2, ranked):**

### Shipped (Most Recent)

- #859, #872: CI-modes product surface — `ci.mode` config, `aesop init --ci-mode`, `aesop doctor` capability table, `aesop runner install|remove` (#859); linux-shape row added to the capability table (#872)
- #872: linux_shape_check gate — distro-aware WSL detection (`wsl -l -q` + `wsl -e true`, not bare `wsl --status`); coexists with G14 in pre-push + CI
- #874: pr_sweep.py — session-independent PR-sweep actor under the 15-min watchdog throttle (closes item 9 residual)
- #870: G14 conflict-marker detection gate (PR #834 incident), wired into pre-push + CI
- #869: main-full.yml Node wiring — isolated-env.mjs into the Node test step (node_harness_wiring_check)
- #871: Merge actor session-independence closed out — dispatch_lint.py no longer contradicts LANE-CONTRACT.md's native-auto-merge policy (item 9, DONE)
- #784: CLI skills installer fails closed; installer fixes its own Windows scaffold gap
- #867: Scheduled-task snapshot diffing (XML fidelity, 5-run stable proof, 4 red-first regression tests)
- #866: dispatch_lint categorical doc exemption (fix for post-#850 red)
- #865: PR symbol survival check (Guardrail G13, non-blocking; promote to blocking after 2026-10-14)
- #864: Node test isolation via isolated-env.mjs
- #863: tripwire hardcoded-path resolution (env-derived conductor root)
- #862: verify-receipt neutral on missing receipt
- #860: pre-push range computation via origin/main (fixes new-branch cases)
- #856: shell-test isolation from conductor3 state
- #855: main-full.yml GitHub schema fix + actionlint (GAP)
- #850: CI throughput: PR head-SHA checkout + Windows path gating
- #854: Receipt gate increments 1–3 (emit, verify, non-required Action)

### In Progress

18. **Watchdog script parity** (IN-FLIGHT). Live watchdog task runs a stale conductor3 copy of `daemons/run-watchdog.sh` — repoint/sync + WATCHDOG-SCRIPT-PARITY selftest (lane in flight).

### Open / Queued

10. **Hard checkpoint+clear enforcement hooks** (QUEUED). Implement pre-push gates to verify STATE.md/BUILDLOG.md are checkpointed before context clears. Enforce single-writer discipline on control files. Matt 2026-10-05 directive.
13. **Receipt gate increments 4–6 — after measurement** (QUEUED). PR #854 shipped increments 1–3. Pending: storage codecs, receipt lineage, signed ledger append.
16. **pyflakes unused-import sweep** (QUEUED). Detect and remove unreferenced imports in driver/, tools/, mcp/, ui/ Python.
17. **LANE-CONTRACT line for AESOP_ALLOW_GENERATED** (QUEUED). Add contract statement permitting lanes to deploy generated-paths registry entries; policy clarification pending.
19. **tools/new_gate_check.py** (QUEUED). One-command new-gate checklist (stub list, inventory parity, claudemd lint/headroom/sync, portability, pre-push self-test) — #872 took 5 red rounds hitting these one at a time; also derive the TTY fixture stub list from `check_*` functions automatically.
20. **tools/INDEX.md union-merge duplicates** (QUEUED). Duplicates accumulate on clean merges due to drift — post-merge regenerate hook or duplicate-tolerant check.
21. **Promote pr_symbol_survival_check (G13) to blocking** (QUEUED). After 2026-10-14 (1 week clean runs since #865).

### Blocked / Deferred

- **FLY_API_TOKEN** (BLOCKED — Matt). Needed for psinasty deploy.
- **Cloudflare zone for dynastywrapped.com** (BLOCKED — Matt). GoDaddy NS cutover + `cloudflared tunnel login`, then signal-hub `deploy/install_tunnel.ps1`.
- **WSL distro install decision** (BLOCKED — Matt). `wsl --install -d Ubuntu` or equivalent; #872's detection fix now correctly reports "no distro" either way.
- **Merchant feed hosting choice** (BLOCKED — Matt). Where tannery-merchant-feed.tsv (claude-scripts #31) gets served from.

**Merge actor session-independence — DONE (2026-10-06, PR #871):** GitHub native auto-merge (armed per-PR at open via `gh pr merge <n> --auto --squash`) is the session-independent merge actor; `AesopMergeQueue` Scheduled Task is correctly `Status: Disabled` — deliberate retirement of the old label+daemon regime. Real gap found and fixed: `tools/dispatch_lint.py` unconditionally forbade `--auto`/bare `gh pr merge`, blocking the exact command LANE-CONTRACT.md requires to arm the merge actor; now `gh pr merge <n> --auto --squash` is explicitly allowed while manual merge, bare `--auto` with no PR number, and `--admin` remain forbidden. 45/45 + 223/223 regression tests green.

**Release-state note:** `v0.7.1` is tagged at `ec5ea9db` and has **no GitHub release** — that commit's CI was red (pre-existing `/api/state` bug). The tag was deliberately NOT moved, since retagging a pushed release rewrites published history. `v0.7.2` is published on npm (Latest, MIT license) and GitHub (Release v0.7.2 Latest). Consumer-visible release history therefore reads 0.7.0 -> 0.7.2; publishing 0.7.1 retroactively is a user decision. `v0.7.1` remains tag-only on git. Current unreleased commits: 219 since v0.7.2 tag (as of 2026-09-11, HEAD 07732210).

**Licensing:** Aesop v0.7.2 and earlier npm artifacts (v0.7.0, v0.7.1, v0.7.2) are MIT-licensed (permissive, commercial use allowed). PR #799 (merged 2026-09-11) relicensed main and HEAD to PolyForm Noncommercial 1.0.0 (research/indie OK, no commercial use). Version v0.7.0 and earlier on npm remain MIT forever.
