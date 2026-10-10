# STATE — Durable System Checkpoint

**What this file is:** The live durable checkpoint that Aesop itself uses during its own `/buildsystem` loop. It records the current system version, architectural decisions, known limitations, and the next milestone. This is not historical archive; it is read by the orchestrator to understand operational state.

**Current Version:** v0.8.0 (tagged + released 2026-09-11; npm latest 0.8.0). HEAD: 4837ae60 (2026-10-05); 73 commits since v0.8.0.

## CURRENT (2026-10-10T18:25Z) - CEILING HOOK REVERTED; FLEET UNFROZEN; HANDOFF FOR /clear
**Read this first.** The 17:34Z block below has the standing plan + NEXT STEPS; this block is the delta since.
**What happened 17:34->18:25Z:** main-context-ceiling.mjs was wired into ~/.claude/settings.json (matcher *) and froze the whole fleet - it denied the orchestrator main thread at ~470K AND, via a broken subagent exemption, denied the background lanes too (they reported "orchestrator context 463K"). Matt had me revert it: `git -C ~/.claude checkout -- settings.json` removed the uncommitted 2-hook wiring (main-context-ceiling + capture-open-findings); the three committed record-discipline hooks (heredoc, scrollback, main-grep) STAY. Fleet is unfrozen.
**main-context-ceiling has THREE bugs - do NOT re-wire until all three are fixed with behavioural proof:** (1) subagent exemption checks an `agent-` transcript basename that does not match real transcript naming, so it measures the parent context; (2) its checkpoint allowlist rejects `printf >> STATE.md` / `>> BUILDLOG.md`, blocking the designed escape; (3) Write of an allowlisted checkpoint file needs a prior Read, but Read is not allowlisted - the escape hatch deadlocks. Only re-wire with matcher * after a live lane is PROVEN allowed while the main thread is denied.
**Lane results since 17:34Z (branches COMPLETE on origin; open a PR each with PR-scoped native auto-merge, verify state==MERGED):** fix/state-md-verifier-ascii-and-drift @ 59ff10a5 (ASCII crash fix + Guardrail #6 drift check + BUILDLOG-resolution fail-open closed; shard 1/4 green 1990 tests; NOT wired into CI yet by design); docs/hooks-flair-notice @ e3c9d597 (Matt's README flair, verbatim, gates green). hooks_wired_gate.py landed in ~/scripts + wired into power_selftest (now exit 0). power_selftest UnicodeDecodeError fixed (scripts 3b8f89d). grep-guard quoted-text false-positive fixed (.claude 7351245).
**On disk NOT pushed - recover before they rot:** aesop-wt-failclosed (pyflakes pre-push made FAIL-CLOSED at pre-push-policy.sh ~1058/~1085, committed, unpushed; fail-open inventory TASK 2 not started); aesop-wt-readers (fleet/dash readers -> canonical CONDUCTOR_ROOT; partial tools/fleet.js + STEP-0-READER-ANALYSIS.md, UNCOMMITTED; remaining readers status.js/mcp/server.mjs/ui/config.py/ui/sse.py/dash/watchdog-gui.sh). checkpoint-hooks fail-closed lane stopped after its STEP 0 fact table: the 6 checkpoint hooks are best-effort (never verify commit/push landed, never write a CHECKPOINT-FAILED marker) - resume to make them fail closed + surfaced by reprime-from-disk.
**WATCHDOG real root cause (RED-provable; first two lanes missed it):** process_repo() in daemons/backup-fleet.sh returns CLEAN when a repo has ONLY untracked files and no unpushed commits - is_touched() flags dirty but process_repo() has no untracked-only branch. Fix process_repo(), not is_touched(); the `-u`/master-wip renames on branch fix/watchdog-bugs @ cf97a379 are NOT the cause.
**Settled facts:** bash.exe AV-deletion fixed, daemons alive. aesop primary tree core.bare was TRUE (hid 6 dirty files, made watchdog skip aesop 4 days) -> set false. conductor3/STATE.md FROZEN since 2026-09-19 (live = aesop/STATE.md); the /power skill step 3 STILL points at the frozen conductor3 STATE.md - FIX THE SKILL. Hook rewrites (updatedInput) are ignored for the Agent tool (#39814/#44412) - only DENY is honored; SubagentStop additionalContext re-wakes agents. Event store aesop/state/tracker_events.db = 572 events newest 2026-10-10T17:35Z. 5 untracked items in aesop primary tree stay under the no-delete rule.
**Open for Matt:** SentinelOne/RMM removal DECLINED by Matt 2026-10-10 (the "exfil" was the cache-read token count misread). npm publish 0.9.1 blocked on 401. SentinelOne exclusion open since 2026-06-23. WSL distro. flyctl IS installed (~/.fly/bin, off PATH); psinasty DID deploy 2026-10-09 - PENDING-DECISIONS has both stale. The "Beyond the Prompt" essay for a Medium/claude artifact was NOT saved (would bloat reprime) - Matt must re-paste; render semantic HTML per memory artifacts-must-paste-into-medium.
**NEXT STEPS:** (1) on resume read this block; (2) fix main-context-ceiling's 3 bugs OR leave it unwired (fleet runs safely without it); (3) open the two PRs + verify MERGED; (4) push aesop-wt-failclosed, finish aesop-wt-readers, resume checkpoint-hooks fail-closed lane; (5) fix watchdog process_repo() untracked-only branch; (6) unify checkpoint writers to the ONE live root; (7) fix the /power skill STATE.md pointer; (8) wire state_md_verifier + gen_state_md into pre-push/CI/selftest once the drift gate passes on the live files. Build nothing new until existing enforcement fires.

## CURRENT (2026-10-10T17:34:09Z) - FAIL-CLOSED RESTORATION; FLEET REVIVED; 9 LANES IN FLIGHT
**Incidents fixed today (2026-10-10):** bash.exe AV-deleted 2026-10-09 22:29 -> restored (usr/bin/sh.exe copy, elevated); both scheduled daemons had failed every tick for ~12.5h -> alive, heartbeats fresh. aesop primary tree had core.bare=true -> set false (it hid 6 dirty files incl. STATE.md and made the watchdog skip aesop for 4 days). Recovered aesop STATE.md pushed as backup/state-recover-20261010 (c96b3982). Three record-discipline hooks wired in ~/.claude/settings.json (8ef3a04) and each proven to DENY. Memory index foreign-profile entries removed and generator fixed (scripts 88c5dcd, .claude 0ed6d5d). ~/CLAUDE.md heartbeat path corrected to conductor3/state.
**Decisions (Matt, 2026-10-10):** NEVER FAIL OPEN - everything fails closed; inert/unwired enforcement is a defect, not a pending decision. The orchestrator is BANNED from deferring a checkpoint for any reason (crash-only). BUILDLOG.md = append-only history, never the current-state source; STATE.md = the only current-state surface; a checkpoint writes BOTH. Orchestrator reads control files only (now hook-enforced). Build nothing new until existing enforcement fires.
**Root cause confirmed (docs lane):** PreToolUse updatedInput is silently ignored for the Agent tool (GitHub #39814, #44412, closed not-planned) -> every rewrite-dependent hook was inert; deny-with-instruction is the only honored path. SubagentStop additionalContext re-wakes agents (documented).
**In flight (lanes):** fix/state-md-verifier-ascii-and-drift verify + Guardrail #6 SKIP->exit0 fail-open; watchdog CLEAN-while-dirty + psinasty WIP pushed at main; pre-push pyflakes fail-closed + fail-open inventory (PR); code-search guard false positive on quoted text; hook-wiring regression gate in ~/scripts wired into power_selftest (reports 4 violations + a UnicodeDecodeError crash to fix); liveness census (three-roots hypothesis: CONDUCTOR_ROOT vs AESOP_ROOT vs aesop-daemon-runtime); rewrite-dependency hook audit; never-fail-open memory; README/npm flair PR.
**Gates awaiting Matt:** npm publish 0.9.1 (401 auth); 5 untracked items in aesop primary tree (mangled filename, 3 nested worktrees, screenshot.js) under the no-delete rule; SentinelOne exclusion (open since 2026-06-23); WSL distro; PENDING-DECISIONS has stale notes (flyctl IS installed at ~/.fly/bin; psinasty DID deploy 2026-10-09 22:01).
**NEXT STEPS:** (1) final-catch every lane report - verify by running the exact gate, never by narrative; (2) land green fixes via PR-scoped auto-merge and verify state == MERGED; (3) wire state_md_verifier + gen_state_md into pre-push/CI/selftest once the drift gate passes on the real files; (4) make the EXISTING checkpoint path (checkpoint-before-compact, SessionEnd, task-event-projector, receipts) commit+push to git fail-closed per the census findings - fix existing, build nothing new; (5) record the crash-only checkpoint ban in memory once the never-fail-open keeper lane lands; (6) re-run power_selftest until it reports OK; (7) resolve the canonical-STATE.md ambiguity if STEP 0 found one.

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

1. **Flaky `test_openai_transport_redirect` characterization** (DONE). Root cause identified and fixed in PR #808. Flaky test no longer reproduces under ci_shard_runner conditions.

2. **Trigger-layer selftest check in power_selftest** (GUARDRAIL #1). Verify scheduled-task execution path during POWER-SELFTEST (fail-closed if conductor3 not cloned or tasks not registered). Addresses fragility noted in Known Limitations.

3. **`test_hook_preflight` rewrite** (DONE). Full rewrite shipped in PR #842: rewrote as `unittest.TestCase` + `tempfile.TemporaryDirectory`, driving `tools/hook_preflight.py` as a real subprocess against hermetic fixtures. 8 tests now collect and run under `python -m unittest discover` (0 before). Tests exit-code semantics: fail-closed on missing/broken interpreter, fail-closed on no repo root, fail-closed on zero checkable files, skip non-shebanged files.

4. **`test_agent_detail_roundtrip` pollution re-verify under ci_shard_runner** (DONE). Verified in PR #843 under real `tools/ci_shard_runner.py` conditions on Windows CI shard (PYTHONUTF8=1, PYTEST_TIMEOUT=120): shard 3/4 round-robin (run twice), test file run alone, shard 3 files with target file ordered last. All runs green; no order-dependent pollution found. Added `test_config_reload_isolates_state_root_per_test` guard asserting `config.STATE_DIR`/`TRANSCRIPTS_ROOT`/`AESOP_ROOT` actually reflect `setUp`'s isolated fixture root; falsifiability-checked (pollution detected when `config.reload()` removed).

5. **STATE.md freshness gate** (DONE). Gate implemented and shipped in PR #809; detects stale checkpoints by comparing Current Version claim vs. HEAD commit count. Baseline freshness checkpoint set 2026-10-05.

6. **Portability path scan (box-restore / trigger-layer absolutization)** (REFACTOR). Ensure all scripts invoked by scheduled tasks use absolute paths (AESOP_HOME or durable ~/scripts location). Validates guardrail proposal from refinesystem R1. Medium effort; medium impact (multi-box readiness).
   Evidence from 2026-09-10 half-restore incident: settings hooks pointed at wrong profile, scheduled-task StartBoundary in past prevented first run, packed-refs/pack loss on restore. Guardrail tracked in PR #793.

7. **Dead-baseline liveness check** (DONE). Shipped in PR #844: `tools/baseline_liveness_check.py` finds every `.*-baseline.json` ratchet baseline and its consumer tool (hardcoded usage in `tools/*.py` or `--baseline` wiring in CI/hooks). Fails closed if baseline has no consumer. `--prune` drops exactly stale entries (shrinks counts, never raises/adds). Real-repo run found `.encoding-baseline.json` dead (encoding_lint never had a `--baseline` flag); removed in PR #844. Other three baselines all have live consumers with zero stale entries. Wired into ci.yml shard 0 next to other ratchet gates.

8. **Stats-refresh PR jam** (RESOLVED). PR #781 (keeper stats) merged. Portfolio stats pipeline validated (2026-10-05). v0.8.0 released with freshness gate active; board catch-up lane started.

9. **Merge actor must not depend on a session** (IN-PROGRESS). Lanes arm native auto-merge at PR open; AesopMergeQueue task disabled. 37-PR board catch-up in progress (2026-10-05). All PRs must merge via GitHub native auto-merge, never by session daemon or manual merge.

10. **Hard checkpoint+clear enforcement hooks** (QUEUED). Implement pre-push gates to verify STATE.md/BUILDLOG.md are checkpointed before context clears. Enforce single-writer discipline on control files. Matt 2026-10-05 directive.

11. **main-full.yml validity fix** (OPEN). PR #855: fix main-full.yml schema errors since PR #850 that produced jobs:[], conclusion:failure runs. Lint GitHub Actions semantics + actionlint. Confirm a main-full run with jobs>0 green when merged.

12. **Merge finishers — PR stacks and board catch-up** (IN-PROGRESS). Board catch-up lane: merge 10-PR batches via serial merge train. Stacks to finish: #738→#739, #745, #754, #777, #784, #832, #833, #834, #849, #852, #856. Auto-merge armed on each PR open; native GitHub auto-merge is the only merge actor (session daemon disabled).

13. **Receipt gate increments 4–6 — after measurement** (QUEUED). PR #854 shipped increments 1-3 (emit, verify, non-required Action). Increments 4–6 pending: storage codecs, receipt lineage, signed ledger append. Medium effort; foundation for auditability + billing transparency.

14. **CI-modes product surface** (IN-FLIGHT). Lane: expose CI run modes and their performance characteristics (dispatch vs. serial, shard allocation, cross-OS drift) as browsable UI panels. Complements cost telemetry + scheduling observability.

15. **Event bridge durable deployment** (BLOCKED). Architecture: Slack webhook integration via Cloudflared tunnel (hostname + auth). Requires Matt to allocate Cloudflare account. Enables: in-band incident notifications, lane-status pings, cost-ceiling alerts.

16. **pyflakes unused-import sweep** (QUEUED). Linting task: detect and remove import statements that are never referenced. Applies to driver/, tools/, mcp/, ui/ Python. Medium effort; code-hygiene improvement, no behavioral change.

17. **LANE-CONTRACT line for AESOP_ALLOW_GENERATED** (BLOCKED). Add contract statement permitting lanes to deploy generated-paths registry entries (tools/generated_paths.json) without manual approval. Requires prior policy clarification (generated-paths governance + merge-driver interaction).

**Release-state note:** `v0.7.1` is tagged at `ec5ea9db` and has **no GitHub release** — that commit's CI was red (pre-existing `/api/state` bug). The tag was deliberately NOT moved, since retagging a pushed release rewrites published history. `v0.7.2` is published on npm (Latest, MIT license) and GitHub (Release v0.7.2 Latest). Consumer-visible release history therefore reads 0.7.0 -> 0.7.2; publishing 0.7.1 retroactively is a user decision. `v0.7.1` remains tag-only on git. Current unreleased commits: 219 since v0.7.2 tag (as of 2026-09-11, HEAD 07732210).

**Licensing:** Aesop v0.7.2 and earlier npm artifacts (v0.7.0, v0.7.1, v0.7.2) are MIT-licensed (permissive, commercial use allowed). PR #799 (merged 2026-09-11) relicensed main and HEAD to PolyForm Noncommercial 1.0.0 (research/indie OK, no commercial use). Version v0.7.0 and earlier on npm remain MIT forever.
